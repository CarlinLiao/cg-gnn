"""
Create and save interactive plots.
"""

from os import makedirs
from os.path import join
from typing import List, Dict

from tqdm import tqdm
from dgl import DGLGraph
from networkx import DiGraph, compose, get_node_attributes

from bokeh.models import Circle, MultiLine, WheelZoomTool, HoverTool, CustomJS, Select, ColorBar
from bokeh.plotting import figure, from_networkx
from bokeh.transform import linear_cmap
from bokeh.palettes import YlOrRd8
from bokeh.layouts import row
from bokeh.io import output_file, save

from cggnn.util.constants import INDICES, FEATURES, PHENOTYPES, CENTROIDS, IMPORTANCES


def _make_bokeh_graph_plot(graph: DiGraph,
                           feature_names: List[str],
                           phenotype_names: List[str],
                           graph_name: str,
                           out_directory: str) -> None:
    "Create bokeh interactive graph visualization."

    # Create bokeh plot and prepare to save it to file
    graph_name = graph_name.split('/')[-1]
    output_file(join(out_directory, graph_name + '.html'),
                title=graph_name)
    f = figure(match_aspect=True, tools=[
        'pan', 'wheel_zoom', 'reset'], title=graph_name)
    f.toolbar.active_scroll = f.select_one(WheelZoomTool)
    mapper = linear_cmap(  # colors nodes according to importance by default
        'importance', palette=YlOrRd8[::-1], low=0, high=1)
    plot = from_networkx(graph, {i_node: dat
                                 for i_node, dat in get_node_attributes(graph, 'centroid').items()})
    plot.node_renderer.glyph = Circle(
        radius='radius', fill_color=mapper, line_width=.1, fill_alpha=.7)
    plot.edge_renderer.glyph = MultiLine(line_alpha=0.2, line_width=.5)

    # Add color legend to right of plot
    colorbar = ColorBar(color_mapper=mapper['transform'], width=8)
    f.add_layout(colorbar, 'right')

    # Define data that shows when hovering over a node/cell
    hover = HoverTool(
        tooltips="h. structure: $index", renderers=[plot.node_renderer])
    hover.callback = CustomJS(
        args=dict(hover=hover,
                  source=plot.node_renderer.data_source),
        code='const feats = ["' + '", "'.join(feature_names) + '"];' +
        'const phenotypes = ["' + '", "'.join(phenotype_names) + '"];' +
        """
        if (cb_data.index.indices.length > 0) {
            const node_index = cb_data.index.indices[0];
            const tooltips = [['h. structure', '$index']];
            for (const feat_name of feats) {
                if (source.data[feat_name][node_index]) {
                    tooltips.push([`${feat_name}`, `@${feat_name}`]);
                }
            }
            for (const phenotype_name of phenotypes) {
                if (source.data[phenotype_name][node_index]) {
                    tooltips.push([`${phenotype_name}`, "1"]);
                }
            }
            hover.tooltips = tooltips;
        }
    """)

    # Add interactive dropdown to change why field nodes are colored by
    color_select = Select(title='Color by property', value='importance', options=[
        'importance'] + feature_names + phenotype_names)
    color_select.js_on_change('value', CustomJS(
        args=dict(source=plot.node_renderer.data_source,
                  cir=plot.node_renderer.glyph),
        code="""
        const field = cb_obj.value;
        cir.fill_color.field = field;
        source.change.emit();
        """)
    )

    # Place components side-by-side and save to file
    layout = row(f, color_select)
    f.renderers.append(plot)
    f.add_tools(hover)
    save(layout)


def _convert_dgl_to_networkx(graph: DGLGraph,
                             feature_names: List[str],
                             phenotype_names: List[str]) -> DiGraph:
    "Convert DGL graph to networkx graph for plotting interactive."

    if IMPORTANCES not in graph.ndata:
        raise ValueError(
            'importance scores not yet found. Run calculate_importance_scores first.')

    graph_networkx = DiGraph()
    for i_g in range(graph.num_nodes()):
        i_gx = graph.ndata[INDICES][i_g].detach(
        ).numpy().astype(int).item()
        graph_networkx.add_node(i_gx)
        feats = graph.ndata[FEATURES][i_g].detach().numpy()
        for j, feat in enumerate(feature_names):
            graph_networkx.nodes[i_gx][feat] = feats[j]
        phenotypes = graph.ndata[PHENOTYPES][i_g].detach().numpy()
        for j, phenotype in enumerate(phenotype_names):
            graph_networkx.nodes[i_gx][phenotype] = phenotypes[j]
        graph_networkx.nodes[i_gx]['importance'] = graph.ndata[IMPORTANCES][i_g].detach(
        ).numpy()
        graph_networkx.nodes[i_gx]['radius'] = graph_networkx.nodes[i_gx][IMPORTANCES]*10
        graph_networkx.nodes[i_gx]['centroid'] = graph.ndata[CENTROIDS][i_g].detach(
        ).numpy()
    return graph_networkx


def _stich_specimen_graphs(graphs: List[DiGraph]) -> DiGraph:
    "Stitch DGL graphs together into a single networkx graph."

    if len(graphs) == 0:
        raise ValueError("Must have at least one graph to stitch.")
    if len(graphs) == 1:
        return graphs[0]
    graph_stitched = graphs[0]
    for graph in graphs[1:]:

        # Check for node overlaps and find the max importance score
        overlap_importance: Dict[int, float] = {
            i: max(graph_stitched.nodes[i]['importance'],
                   graph.nodes[i]['importance'])
            for i in set(graph_stitched.nodes).intersection(graph.nodes)
        }

        # Stich the next graph into the collected graph
        graph_stitched = compose(graph_stitched, graph)

        # Overwrite the max importance score.
        for i, importance in overlap_importance.items():
            graph_stitched.nodes[i]['importance'] = importance

    return graph_stitched


def generate_interactives(graphs_to_plot: Dict[str, List[DGLGraph]],
                          feature_names: List[str],
                          phenotype_names: List[str],
                          out_directory: str
                          ) -> None:
    "Create bokeh interactive plots for all graphs in the out_directory."
    makedirs(out_directory, exist_ok=True)
    for name, dgl_graphs in tqdm(graphs_to_plot.items()):
        graphs = [_convert_dgl_to_networkx(
            graph, feature_names, phenotype_names) for graph in dgl_graphs]
        _make_bokeh_graph_plot(_stich_specimen_graphs(graphs),
                               feature_names, phenotype_names, name, out_directory)
