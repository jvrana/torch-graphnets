import numpy as np
import torch

from archived.pyro_graph_nets import Aggregator
from archived.pyro_graph_nets import EdgeBlock
from archived.pyro_graph_nets import GlobalBlock
from archived.pyro_graph_nets import MLP
from archived.pyro_graph_nets import NodeBlock
from archived.pyro_graph_nets import generate_networkx_graphs
from archived.pyro_graph_nets import Flex
from archived.pyro_graph_nets import GraphEncoder
from archived.pyro_graph_nets import GraphNetwork
from archived.pyro_graph_nets.utils import GraphDataLoader
from archived.pyro_graph_nets.utils import GraphDataset
from archived.pyro_graph_nets.utils import cat_gt
from archived.pyro_graph_nets.utils import to_graph_tuple

# input_node_fields = ("pos", "weight", "start", "end")
# input_edge_fields = ("distance",)
# target_node_fields = ("solution",)
# target_edge_fields = ("solution",)


class EncodeProcessDecode(torch.nn.Module):
    def __init__(self):
        super().__init__()
        FlexMLP = Flex(MLP)
        self.encoder = GraphEncoder(
            EdgeBlock(FlexMLP(Flex.d(), 16, 16), independent=True),
            NodeBlock(FlexMLP(Flex.d(), 16, 16), independent=True),
            None,
        )

        # note that core should have the same output dimensions as the encoder
        self.core = GraphNetwork(
            EdgeBlock(FlexMLP(Flex.d(), 16, 16), independent=False),
            NodeBlock(
                FlexMLP(Flex.d(), 16, 16),
                independent=False,
                edge_aggregator=Aggregator("mean"),
            ),
            GlobalBlock(FlexMLP(Flex.d(), 1), independent=True),
        )

        self.decoder = GraphEncoder(
            EdgeBlock(FlexMLP(Flex.d(), 16, 1), independent=True),
            NodeBlock(FlexMLP(Flex.d(), 16, 1), independent=True),
            GlobalBlock(MLP(1, 1), independent=True),
        )

        # self.output_transform = GraphEncoder(
        #     EdgeBlock(Flex(torch.nn.Linear)(Flex.d(), 1), independent=True),
        #     NodeBlock(Flex(torch.nn.Linear)(Flex.d(), 1), independent=True),
        #     GlobalBlock(
        #         MLP(1, 1),
        #         independent=True
        #     )
        # )

    def forward(self, input_gt, num_steps: int):
        latent = self.encoder(input_gt)
        latent0 = latent

        output = []
        for step in range(num_steps):
            core_input = cat_gt(latent0, latent)
            latent = self.core(core_input)
            decoded = self.decoder(latent)
            # out = self.output_transform(decoded)
            output.append(decoded)
        return output

def test_tensorboard(new_writer):

    rand = np.random.RandomState(3)
    writer = new_writer("shortest_path", suffix='_tb_test')

    # preprocessed graphs
    def preprocess(graphs):
        for graph in graphs:
            for _, ndata in graph.nodes(data=True):
                ndata["x"] = torch.tensor(ndata["features"][:3])
                ndata["y"] = torch.tensor([ndata["solution"]])

            for _, _, edata in graph.edges(data=True):
                edata["x"] = torch.tensor(edata["features"])
                edata["y"] = torch.tensor([edata["solution"]])

    # training loader
    input_graphs, _, _ = generate_networkx_graphs(rand, 100, (2, 50), 20)
    preprocess(input_graphs)

    model = EncodeProcessDecode()

    input_gt = to_graph_tuple(input_graphs[:1], feature_key="x")
    with torch.no_grad():
        outputs = model(input_gt, 10)

    writer.add_histogram('out[0].nodeattr', outputs[0].node_attr)
    writer.add_histogram('out[-1].nodeattr', outputs[-1].node_attr)

    encoded = model.encoder(input_gt)

    writer.add_histogram('encoded.nodeattr', outputs[0].node_attr)

def test_shortest_path_examples(new_writer):

    writer = new_writer("shortest_path")

    rand = np.random.RandomState(2)
    input_graphs, target_graphs, _ = generate_networkx_graphs(rand, 10, (2, 20), 20)

    nodes = list(input_graphs[0].nodes(data=True))
    print(nodes[0])

    edges = list(input_graphs[0].edges(data=True))
    print(edges[0])

    # preprocessed graphs
    def preprocess(graphs):
        for graph in graphs:
            for _, ndata in graph.nodes(data=True):
                ndata["x"] = torch.tensor(ndata["features"][:3])
                ndata["y"] = torch.tensor([ndata["solution"]])

            for _, _, edata in graph.edges(data=True):
                edata["x"] = torch.tensor(edata["features"])
                edata["y"] = torch.tensor([edata["solution"]])

    # training loader
    input_graphs, _, _ = generate_networkx_graphs(rand, 1000, (2, 50), 20)
    preprocess(input_graphs)
    dataset = GraphDataset(input_graphs)
    n_train = int((len(dataset) * 0.9))
    n_test = len(dataset) - n_train
    train_set, test_set = torch.utils.data.random_split(dataset, [n_train, n_test])
    loader = GraphDataLoader(train_set, batch_size=100, shuffle=True)
    test_loader = GraphDataLoader(test_set, batch_size=100, shuffle=False)

    model = EncodeProcessDecode()

    device = "cuda:0"
    # prime the model
    input_gt = to_graph_tuple([dataset[0]], feature_key="x")
    with torch.no_grad():
        model(input_gt, 10)

    # writer.add_graph(model, (input_gt, 10))

    model.to(device)
    optimizer = torch.optim.Adam(lr=0.02, params=model.parameters())
    criterion = torch.nn.BCEWithLogitsLoss()

    def loss_fn(outputs, target_gt):
        return [
            criterion(out.node_attr, target_gt.node_attr) + \
            criterion(out.edge_attr, target_gt.edge_attr
        ) for out in outputs]

    running_loss = 0.0
    num_epochs = 300
    num_steps = 10

    log_every_epoch = 10

    for epoch in range(num_epochs):

        # min batch
        for batch_ndx, bg in enumerate(loader):
            input_gt = to_graph_tuple(bg, feature_key="x", device=device)
            target_gt = to_graph_tuple(bg, feature_key="y", device=device)

            # zero the parameter gradients
            optimizer.zero_grad()

            # forward + backward + optimize
            outputs = model(input_gt, num_steps)
            loss = sum(loss_fn(outputs, target_gt)) / num_steps
            loss.backward()
            optimizer.step()
            running_loss += loss.item()

        with torch.no_grad():
            running_test_loss = 0.0
            for test_batch in test_loader:
                test_input_gt = to_graph_tuple(
                    test_batch, feature_key="x", device=device
                )
                test_target_gt = to_graph_tuple(
                    test_batch, feature_key="y", device=device
                )

                test_outputs = model(test_input_gt, num_steps)
                test_loss = loss_fn(test_outputs, test_target_gt)[-1]
                running_test_loss += test_loss.item()

                writer.add_histogram('output_node_attr_0', test_outputs[0].node_attr)
                writer.add_histogram('output_node_attr_-1', test_outputs[-1].node_attr)

                writer.add_histogram('output_edge_attr_0', test_outputs[0].edge_attr)
                writer.add_histogram('output_edge_attr_-1', test_outputs[-1].edge_attr)
        writer.add_scalar("test_loss", running_test_loss, epoch)

        writer.add_scalar("training loss", running_loss, epoch)
        running_loss = 0.0

        if epoch % log_every_epoch == 0:
            pass