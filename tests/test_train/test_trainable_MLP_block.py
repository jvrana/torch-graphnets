import functools

import pytest
import torch
import torch.optim as optim

from pyrographnets.blocks import Flex, MLP
from pyrographnets.data import GraphData, GraphDataLoader
from typing import Dict, Any, Callable, TypeVar, Hashable, Tuple

networks = {
    'mlp': lambda: torch.nn.Sequential(
        Flex(MLP)(Flex.d(), 16, layer_norm=False),
        torch.nn.Linear(16, 1)
    ),
    'linear': lambda: torch.nn.Sequential(
        torch.nn.Linear(5, 16),
        torch.nn.ReLU(),
        torch.nn.Linear(16, 1)
    )
}


def parametrize_dict(name, d: Dict[Hashable, Any], **kwargs) -> Callable:
    return pytest.mark.parametrize(name, list(d.values()), ids=list(d.keys()), **kwargs)


@pytest.fixture
def network(request, device):
    net = request.param()
    return net.to(device)

def to(x, device):
    if device is None:
        return x
    else:
        return x.to(device)

def train(net, loader, epochs, optimizer,
          criterion, batch_to_input, batch_to_target, device: None):
    loss_arr = torch.zeros(epochs)
    for epoch in range(epochs):
        net.train()
        running_loss = 0.
        for batch in loader:
            if device:
                batch = batch.to(device)
            input = batch_to_input(batch)
            target = batch_to_target(batch)

            optimizer.zero_grad()  # zero the gradient buffers
            output = net(input)
            loss = criterion(output, target)
            loss.backward()
            optimizer.step()

            running_loss += loss.item()
        loss_arr[epoch] = running_loss
    return loss_arr


class DataModifier(object):

    def __init__(self, modify, to_input, to_output):
        self.modify = modify
        self.to_input = to_input
        self.to_output = to_output

    @staticmethod
    def _func(datalist, attr, f):
        # make data trainable
        for data in datalist:
            val1 = getattr(data, attr)
            val2 = f(val1)
            setattr(data, attr, torch.cat([val1, val2], axis=1))
        return datalist


modifiers = {
    'node_sum': DataModifier(
        functools.partial(DataModifier._func, attr='x',
                          f=lambda x: x.sum(axis=1, keepdims=True)),
        lambda batch: batch.view(slice(None, -1), None, None).x,
        lambda batch: batch.view(slice(-1, None), None, None).x
    ),
    'node_prod': DataModifier(
        functools.partial(DataModifier._func, attr='x',
                          f=lambda x: x.prod(axis=1, keepdims=True)),
        lambda batch: batch.view(slice(None, -1), None, None).x,
        lambda batch: batch.view(slice(-1, None), None, None).x
    )
}

@parametrize_dict('network', networks, indirect=True)
@parametrize_dict('modifier', modifiers)
def test_train_mlp(network, modifier, device):
    """Trains a MLP for the very simple function of addition"""
    epochs = 25
    datalist = [GraphData.random(5, 5, 5, requires_grad=True) for _ in range(1000)]

    # modify datalist
    modifier.modify(datalist)

    # create loader
    loader = GraphDataLoader(datalist, batch_size=100)

    # provide example
    example = to(modifier.to_input(loader.first()), device)
    network(example)

    optimizer = optim.AdamW(network.parameters(), lr=1e-1)
    criterion = torch.nn.MSELoss()

    losses = train(network, loader, epochs, optimizer, criterion, modifier.to_input,
                   modifier.to_output, device)
    print(losses)
    assert 0.01 * losses[-1] < losses[0]
