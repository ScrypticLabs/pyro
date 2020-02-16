# Copyright Contributors to the Pyro project.
# SPDX-License-Identifier: Apache-2.0

from functools import singledispatch

import torch

import pyro.distributions as dist
from pyro.poutine.messenger import Messenger
from pyro.poutine.util import site_is_subsample


class PrefixReplayMessenger(Messenger):
    """
    EXPERIMENTAL Given a trace of training data, replay a model with batched
    sites extended to include both training and forecast time, using the guide
    trace for the training prefix and samples from the prior on the forecast
    postfix.

    :param trace: a guide trace.
    :type trace: ~pyro.poutine.trace_struct.Trace
    """
    def __init__(self, trace):
        super().__init__()
        self.trace = trace

    def _pyro_post_sample(self, msg):
        if site_is_subsample(msg):
            return

        name = msg["name"]
        if name not in self.trace:
            return

        model_value = msg["value"]
        guide_value = self.trace.nodes[name]["value"]
        if model_value.shape == guide_value.shape:
            msg["value"] = guide_value
            return

        assert model_value.dim() >= 2
        assert model_value.dim() == guide_value.dim()
        assert model_value.shape[:-2] == guide_value.shape[:-2]
        assert model_value.size(-2) > guide_value.size(-2)
        assert model_value.size(-1) == guide_value.size(-1)
        assert any(f.name == "time" for f in msg["cond_indep_stack"])
        split = guide_value.size(-2)
        msg["value"] = torch.cat([guide_value, model_value[..., split:, :]], dim=-2)


@singledispatch
def prefix_condition(d, data):
    """
    EXPERIMENTAL Given a distribution ``d`` of shape ``batch_shape + (t+f, d)``
    and data ``x`` of shape ``batch_shape + (t, d)``, find a conditional
    distribution of shape ``batch_shape + (f, d)``. Typically ``t`` is the
    number of training time steps, ``f`` is the number of forecast time steps,
    and ``d`` is the data dimension.

    :param d: a distribution with ``len(d.shape()) >= 2``
    :type d: ~pyro.distributions.Distribution
    :param data: data of dimension at least 2.
    :type data: ~torch.Tensor
    """
    return d.prefix_condition(data)
    try:
        return d.prefix_condition(data)
    except AttributeError:
        raise NotImplementedError("prefix_condition() does not suport {}".format(type(d)))


@prefix_condition.register(dist.Independent)
def _(d, data):
    base_dist = prefix_condition(d.base_dist, data)
    return base_dist.to_event(d.reinterpreted_batch_ndims)


UNIVARIATE_DISTS = [
    (dist.Cauchy, ("loc", "scale")),
    (dist.Laplace, ("loc", "scale")),
    (dist.Normal, ("loc", "scale")),
    (dist.Stable, ("stability", "skew", "scale", "loc")),
    (dist.StudentT, ("df", "loc", "scale")),
]

for _type, _params in UNIVARIATE_DISTS:

    @prefix_condition.register(_type)
    def _(d, data, _type=_type, _params=_params):
        t = data.size(-2)
        params = [getattr(d, name)[..., t:, :] for name in _params]
        return type(d)(*params)