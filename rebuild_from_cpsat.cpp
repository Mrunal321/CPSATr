#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
#include <unordered_map>
#include <vector>

#include <kitty/dynamic_truth_table.hpp>
#include <kitty/operations.hpp>

#include <lorina/blif.hpp>

#include <nlohmann/json.hpp>

#include <mockturtle/algorithms/cut_enumeration.hpp>
#include <mockturtle/io/blif_reader.hpp>
#include <mockturtle/io/write_blif.hpp>
#include <mockturtle/networks/klut.hpp>
#include <mockturtle/views/names_view.hpp>

int main( int argc, char** argv )
{
  using namespace mockturtle;

  if ( argc != 5 )
  {
    std::cerr << "Usage: rebuild_from_cpsat <input.blif> <cuts.json> <chosen_cuts.json> <output.blif>\n";
    return 1;
  }

  const std::string input_blif = argv[1];
  const std::string cuts_json_path = argv[2];
  const std::string chosen_json_path = argv[3];
  const std::string output_blif = argv[4];

  nlohmann::json cuts_json;
  {
    std::ifstream cuts_stream( cuts_json_path );
    if ( !cuts_stream )
    {
      std::cerr << "Cannot open cuts JSON '" << cuts_json_path << "'\n";
      return 2;
    }
    cuts_stream >> cuts_json;
  }

  if ( !cuts_json.contains( "cuts_per_node" ) || !cuts_json.contains( "nodes" ) )
  {
    std::cerr << "Invalid cuts JSON: missing required fields\n";
    return 2;
  }

  uint32_t cut_size = cuts_json["cuts_per_node"].get<uint32_t>();
  std::vector<std::string> output_names;
  if ( cuts_json.contains( "outputs" ) )
  {
    output_names = cuts_json["outputs"].get<std::vector<std::string>>();
  }

  nlohmann::json chosen_json;
  {
    std::ifstream chosen_stream( chosen_json_path );
    if ( !chosen_stream )
    {
      std::cerr << "Cannot open chosen cuts JSON '" << chosen_json_path << "'\n";
      return 2;
    }
    chosen_stream >> chosen_json;
  }

  if ( !chosen_json.contains( "chosen_cuts" ) || !chosen_json["chosen_cuts"].is_object() )
  {
    std::cerr << "Invalid chosen cuts JSON: missing 'chosen_cuts' object\n";
    return 2;
  }

  names_view<klut_network> ntk;
  blif_reader reader( ntk );
  if ( lorina::read_blif( input_blif, reader ) != lorina::return_code::success )
  {
    std::cerr << "Failed to read BLIF '" << input_blif << "'\n";
    return 3;
  }

  cut_enumeration_params ps;
  ps.cut_size = cut_size;
  ps.cut_limit = 32;
  auto cuts = cut_enumeration<names_view<klut_network>, true>( ntk, ps );

  std::vector<std::string> node_names( ntk.size() );
  std::unordered_map<std::string, uint32_t> name_to_index;

  uint32_t pi_index = 0u;
  ntk.foreach_pi( [&]( auto const& signal ) {
    const auto node = ntk.get_node( signal );
    const auto idx = ntk.node_to_index( node );
    std::string name = ntk.has_name( signal ) ? ntk.get_name( signal ) : "";
    if ( name.empty() )
    {
      name = "pi" + std::to_string( pi_index );
    }
    node_names[idx] = name;
    name_to_index[name] = idx;
    ++pi_index;
  } );

  ntk.foreach_node( [&]( auto const& node ) {
    const auto idx = ntk.node_to_index( node );
    if ( ntk.is_constant( node ) )
    {
      node_names[idx] = ntk.constant_value( node ) ? "const1" : "const0";
      name_to_index[node_names[idx]] = idx;
      return;
    }
    if ( node_names[idx].empty() )
    {
      node_names[idx] = "n" + std::to_string( idx );
    }
    name_to_index[node_names[idx]] = idx;
  } );

  std::unordered_map<std::string, uint32_t> chosen_cut_index;
  for ( auto it = chosen_json["chosen_cuts"].begin(); it != chosen_json["chosen_cuts"].end(); ++it )
  {
    chosen_cut_index[it.key()] = it.value().get<uint32_t>();
  }

  std::unordered_map<uint32_t, uint32_t> index_to_chosen_cut;
  for ( auto const& [name, cut_idx] : chosen_cut_index )
  {
    auto idx_it = name_to_index.find( name );
    if ( idx_it == name_to_index.end() )
    {
      std::cerr << "Warning: chosen cut references unknown node '" << name << "'\n";
      continue;
    }
    index_to_chosen_cut[idx_it->second] = cut_idx;
  }

  names_view<klut_network> new_ntk;
  std::unordered_map<uint32_t, decltype( new_ntk )::signal> index_to_new_signal;
  std::unordered_map<std::string, decltype( new_ntk )::signal> pi_name_to_signal;

  const auto const0_signal = ntk.get_constant( false );
  const auto const0_node = ntk.get_node( const0_signal );
  index_to_new_signal[ntk.node_to_index( const0_node )] = new_ntk.get_constant( false );

  const auto const1_signal = ntk.get_constant( true );
  const auto const1_node = ntk.get_node( const1_signal );
  index_to_new_signal[ntk.node_to_index( const1_node )] = new_ntk.get_constant( true );

  ntk.foreach_pi( [&]( auto const& signal ) {
    const auto node = ntk.get_node( signal );
    const auto idx = ntk.node_to_index( node );
    auto const& pi_name = node_names[idx];
    auto new_sig = new_ntk.create_pi( pi_name );
    index_to_new_signal[idx] = new_sig;
    pi_name_to_signal[pi_name] = new_sig;
  } );

  uint32_t selected_nodes = 0u;
  ntk.foreach_node( [&]( auto const& node ) {
    if ( ntk.is_constant( node ) || ntk.is_pi( node ) )
    {
      return;
    }
    const auto idx = ntk.node_to_index( node );
    auto chosen_it = index_to_chosen_cut.find( idx );
    if ( chosen_it == index_to_chosen_cut.end() )
    {
      return;
    }

    const auto& cuts_for_node = cuts.cuts( node );
    auto cut_count = static_cast<uint32_t>( cuts_for_node.size() );
    if ( chosen_it->second >= cut_count )
    {
      std::cerr << "Warning: chosen cut index " << chosen_it->second << " out of range for node " << node_names[idx] << "\n";
      return;
    }

    auto cut_iter = cuts_for_node.begin();
    std::advance( cut_iter, chosen_it->second );
    auto const& cut = **cut_iter;

    std::vector<decltype( new_ntk )::signal> leaf_signals;
    bool missing_leaf = false;
    for ( auto leaf : cut )
    {
      const auto leaf_idx = leaf;
      auto map_it = index_to_new_signal.find( leaf_idx );
      if ( map_it == index_to_new_signal.end() )
      {
        std::cerr << "Warning: missing mapped leaf for node " << node_names[idx] << "\n";
        missing_leaf = true;
        break;
      }
      leaf_signals.push_back( map_it->second );
    }
    if ( missing_leaf )
    {
      return;
    }

    auto tt = cuts.truth_table( cut );
    auto new_sig = new_ntk.create_node( leaf_signals, tt );
    index_to_new_signal[idx] = new_sig;
    ++selected_nodes;
  } );

  for ( auto const& out_name : output_names )
  {
    bool created = false;

    auto idx_it = name_to_index.find( out_name );
    if ( idx_it != name_to_index.end() )
    {
      auto sig_it = index_to_new_signal.find( idx_it->second );
      if ( sig_it != index_to_new_signal.end() )
      {
        new_ntk.create_po( sig_it->second, out_name );
        created = true;
      }
    }

    if ( !created )
    {
      auto pi_it = pi_name_to_signal.find( out_name );
      if ( pi_it != pi_name_to_signal.end() )
      {
        new_ntk.create_po( pi_it->second, out_name );
        created = true;
      }
    }

    if ( !created )
    {
      std::cerr << "[warn] could not create PO for " << out_name << "\n";
    }
  }

  write_blif( new_ntk, output_blif );

  std::cout << "Original nodes: " << ntk.size() << "\n";
  std::cout << "Rebuilt nodes:  " << new_ntk.size() << "\n";
  std::cout << "Rebuilt PIs:    " << new_ntk.num_pis() << "\n";
  std::cout << "Rebuilt POs:    " << new_ntk.num_pos() << "\n";
  std::cout << "Selected nodes: " << selected_nodes << "\n";

  return 0;
}
