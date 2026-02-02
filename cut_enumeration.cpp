#include <algorithm>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#include <mockturtle/networks/klut.hpp>
#include <mockturtle/views/names_view.hpp>
#include <mockturtle/views/fanout_view.hpp>
#include <mockturtle/io/blif_reader.hpp>
#include <mockturtle/algorithms/cut_enumeration.hpp>

#include <kitty/dynamic_truth_table.hpp>
#include <kitty/operations.hpp>

#include <lorina/blif.hpp>
#include <nlohmann/json.hpp>

namespace
{
uint32_t compute_inv_cost( kitty::dynamic_truth_table const& tt )
{
  uint32_t cost = 0;
  const auto num_vars = tt.num_vars();
  for ( unsigned var = 0; var < num_vars; ++var )
  {
    auto tt0 = kitty::cofactor0( tt, var );
    auto tt1 = kitty::cofactor1( tt, var );
    auto bad_pos = tt0 & ~tt1;
    auto bad_neg = tt1 & ~tt0;
    if ( !kitty::is_const0( bad_pos ) && !kitty::is_const0( bad_neg ) )
    {
      ++cost;
    }
  }
  return cost;
}
} // namespace

int main( int argc, char** argv )
{
  constexpr uint32_t kCutLimit = 32;
  using namespace mockturtle;

  if ( argc < 3 )
  {
    std::cerr << "Usage: cut_enumeration <input.blif> <output.json> [K]\n";
    return 1;
  }

  std::string const blif_file  = argv[1];
  std::string const json_file  = argv[2];
  int K = 4;
  if ( argc >= 4 )
  {
    K = std::atoi( argv[3] );
    if ( K <= 0 ) K = 4;
  }

  // 1. Read BLIF into KLUT network
  klut_network klut;
  names_view<klut_network> ntk{ klut };

  {
    blif_reader reader( ntk );
    auto result = lorina::read_blif( blif_file, reader );
    if ( result != lorina::return_code::success )
    {
      std::cerr << "Error reading BLIF\n";
      return 1;
    }
  }

  fanout_view<names_view<klut_network>> fntk{ ntk };

  std::cerr << "[info] PIs=" << ntk.num_pis()
            << " POs=" << ntk.num_pos()
            << " nodes=" << ntk.size()
            << "  K=" << K << "\n";

  // 2. Cut enumeration
  cut_enumeration_params ps;
  ps.cut_size = K;
  ps.cut_limit = kCutLimit;
  auto cut_res = cut_enumeration<names_view<klut_network>, true>( ntk, ps );

  nlohmann::json j;
  j["nodes"] = nlohmann::json::array();
  j["outputs"] = nlohmann::json::array();
  j["cuts_per_node"] = ps.cut_size;

  // 3. Name each node; mark PIs
  std::vector<std::string> node_names( ntk.size() );
  std::vector<bool> is_pi( ntk.size(), false );

  ntk.foreach_pi( [&]( auto const& s, auto /*index*/ ){
    auto n   = ntk.get_node( s );
    auto idx = ntk.node_to_index( n );
    auto name = ntk.get_name( s );  // e.g. opcode[0]
    node_names[idx] = name;
    is_pi[idx] = true;
  } );

  ntk.foreach_node( [&]( auto n ){
    auto idx = ntk.node_to_index( n );
    if ( node_names[idx].empty() )
    {
      if ( ntk.is_constant( n ) )
      {
        node_names[idx] = "const" + std::to_string( idx );
      }
      else
      {
        node_names[idx] = "n" + std::to_string( idx );
      }
    }
  } );

  // 4. Try to use real POs
  uint32_t po_count = ntk.num_pos();
  if ( po_count > 0 )
  {
    ntk.foreach_po( [&]( auto const& s, auto index ){
      auto n   = ntk.get_node( s );
      auto idx = ntk.node_to_index( n );
      auto name = node_names[idx];
      j["outputs"].push_back( name );
    } );
  }
  else
  {
    // 5. Fallback: no POs in network → treat fanout-0 nodes (incl. PIs) as outputs
    std::cerr << "[warn] Network has 0 POs. Using fanout-0 nodes as outputs.\n";

    fntk.foreach_node( [&]( auto n ){
      if ( fntk.is_constant( n ) ) return;

      auto idx = fntk.node_to_index( n );

      // We *include* PIs now as possible outputs, so no is_pi[idx] check here.
      if ( fntk.fanout_size( n ) == 0 )
      {
        auto const& name = node_names[idx];
        j["outputs"].push_back( name );
        std::cerr << "[OUT] fanout-0 idx=" << idx
                  << " name=" << name << "\n";
      }
    } );

  }

  std::cerr << "[info] Exporting " << j["outputs"].size() << " outputs\n";

  // 6. Export internal nodes and their cuts
  ntk.foreach_node( [&]( auto n ){
    if ( ntk.is_constant( n ) )
      return;

    auto idx = ntk.node_to_index( n );
    if ( is_pi[idx] )
      return; // PIs are only leaves

    nlohmann::json nd;
    nd["index"] = idx;
    nd["name"]  = node_names[idx];

    nlohmann::json cuts_json = nlohmann::json::array();
    auto const& cuts_for_node = cut_res.cuts( n );

    for ( auto it_cut = cuts_for_node.begin(); it_cut != cuts_for_node.end(); ++it_cut )
    {
      auto const& cut = **it_cut;

      nlohmann::json leaves = nlohmann::json::array();
      for ( auto const& leaf_node : cut )
      {
        auto leaf_idx = ntk.node_to_index( leaf_node );
        leaves.push_back( node_names[leaf_idx] );
      }

      auto tt = cut_res.truth_table( cut );
      auto inv_cost = compute_inv_cost( tt );

      nlohmann::json cut_obj;
      cut_obj["leaves"] = leaves;
      cut_obj["inv_cost"] = inv_cost;
      cut_obj["depth_cost"] = 1;
      cut_obj["area_cost"] = static_cast<uint32_t>( leaves.size() );
      cuts_json.push_back( std::move( cut_obj ) );
    }

    nd["cuts"] = cuts_json;
    j["nodes"].push_back( nd );
  } );

  std::ofstream ofs( json_file );
  ofs << j.dump( 2 ) << std::endl;

  return 0;
}
