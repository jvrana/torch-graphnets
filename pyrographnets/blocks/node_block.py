import torch
from torch import nn

from pyrographnets.blocks.block import Block
from pyrographnets.blocks.aggregator import Aggregator
from pyrographnets.data import GraphBatch


class NodeBlock(Block):

    def __init__(self, mlp: nn.Module):
        super().__init__({
            'mlp': mlp
        }, independent=True)

    def forward(self, node_attr):
        return self.block_dict['mlp'](node_attr)

    def forward_from_data(self, data: GraphBatch):
        return self(data.x)


class AggregatingNodeBlock(NodeBlock):

    def __init__(self, mlp: nn.Module, edge_aggregator: Aggregator):
        super().__init__(mlp)
        self.block_dict['edge_aggregator'] = edge_aggregator
        self._independent = False

    # TODO: source_to_target, target_to_source
    def forward(self, node_attr, edge_attr, edges):
        # source_to_dest or dest_to_source
        aggregated = self.block_dict['edge_aggregator'](edge_attr, edges[1], dim=0, dim_size=node_attr.size(0))
        out = torch.cat([node_attr, aggregated], dim=1)
        return self.block_dict['mlp'](out)

    def forward_from_data(self, data: GraphBatch):
        return self(data.x, data.e, data.edges)