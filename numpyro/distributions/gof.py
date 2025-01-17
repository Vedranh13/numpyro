# Copyright Contributors to the Pyro project.
# Copyright (c) 2015, Gamelan Labs, Inc.
# Copyright (c) 2016, Google, Inc.
# Copyright (c) 2019, Gamalon, Inc.
# Copyright Contributors to the Pyro project.
# SPDX-License-Identifier: Apache-2.0

# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# - Redistributions of source code must retain the above copyright
#   notice, this list of conditions and the following disclaimer.
# - Redistributions in binary form must reproduce the above copyright
#   notice, this list of conditions and the following disclaimer in the
#   documentation and/or other materials provided with the distribution.
# - Neither the name of Salesforce.com nor the names of its contributors
#   may be used to endorse or promote products derived from this
#   software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
# OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR
# TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE
# USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Goodness of Fit Testing
-----------------------

This module implements goodness of fit tests for checking agreement between
distributions' ``.sample()`` and ``.log_prob()`` methods. The main functions
return a goodness of fit p-value ``gof`` which for good data should be
``Uniform(0,1)`` distributed and for bad data should be close to zero. To use
this returned number in tests, set a global variable ``TEST_FAILURE_RATE`` to
something smaller than 1 / number of tests in your suite, then in each test
assert ``gof > TEST_FAILURE_RATE``. For example::

    TEST_FAILURE_RATE = 1 / 20  # For 1 in 20 chance of spurious failure.

    def test_my_distribution():
        d = MyDistribution()
        samples = d.sample([10000])
        probs = d.log_prob(samples).exp()
        gof = auto_goodness_of_fit(samples, probs)
        assert gof > TEST_FAILURE_RATE

This module is a port of the
`goftests <https://github.com/posterior/goftests>`_ library.
"""

import math
import sys
import warnings

import numpy as np

import jax

from numpyro.util import find_stack_level

HISTOGRAM_WIDTH = 60


class InvalidTest(ValueError):
    pass


def print_histogram(probs, counts):
    max_count = max(counts)
    print("{: >8} {: >8}".format("Prob", "Count"))
    for prob, count in sorted(zip(probs, counts), reverse=True):
        width = int(round(HISTOGRAM_WIDTH * count / max_count))
        print("{: >8.3f} {: >8d} {}".format(prob, count, "-" * width))


def multinomial_goodness_of_fit(probs, counts, *, total_count=None, plot=False):
    """
    Pearson's chi^2 test, on possibly truncated data.
    https://en.wikipedia.org/wiki/Pearson%27s_chi-squared_test

    :param numpy.ndarray probs: Vector of probabilities.
    :param numpy.ndarray counts: Vector of counts.
    :param int total_count: Optional total count in case data is truncated,
        otherwise None.
    :param bool plot: Whether to print a histogram. Defaults to False.
    :returns: p-value of truncated multinomial sample.
    :rtype: float
    """
    probs = jax.lax.stop_gradient(probs)
    assert len(probs.shape) == 1
    assert probs.shape == counts.shape
    if total_count is None:
        truncated = False
        total_count = int(counts.sum())
    else:
        truncated = True
        assert total_count >= counts.sum()
    if plot:
        print_histogram(probs, counts)

    chi_squared = 0
    dof = 0
    for p, c in zip(probs.tolist(), counts.tolist()):
        if abs(p - 1) < 1e-8:
            return 1 if c == total_count else 0
        assert p < 1, f"bad probability: {p:g}"
        if p > 0:
            mean = total_count * p
            variance = total_count * p * (1 - p)
            if not (variance > 1):
                raise InvalidTest("Goodness of fit is inaccurate; use more samples")
            chi_squared += (c - mean) ** 2 / variance
            dof += 1
        else:
            warnings.warn(
                "Zero probability in goodness-of-fit test",
                stacklevel=find_stack_level(),
            )
            if c > 0:
                return math.inf

    if not truncated:
        dof -= 1

    survival = _chi2sf(chi_squared, dof)
    return survival


def unif01_goodness_of_fit(samples, *, plot=False):
    """
    Bin uniformly distributed samples and apply Pearson's chi^2 test.

    :param numpy.ndarray samples: A vector of real-valued samples from a
        candidate distribution that should be Uniform(0, 1)-distributed.
    :param bool plot: Whether to print a histogram. Defaults to False.
    :returns: Goodness of fit, as a p-value.
    :rtype: float
    """
    samples = jax.lax.stop_gradient(samples)
    assert samples.min() >= 0
    assert samples.max() <= 1
    bin_count = int(round(len(samples) ** 0.333))
    if bin_count < 7:
        raise InvalidTest("imprecise test, use more samples")
    probs = np.ones(bin_count) / bin_count
    binned = (samples * bin_count).astype(int)
    binned = np.clip(binned, 0, bin_count - 1)
    counts = np.bincount(binned, minlength=bin_count)
    return multinomial_goodness_of_fit(probs, counts, plot=plot)


def exp_goodness_of_fit(samples, plot=False):
    """
    Transform exponentially distribued samples to Uniform(0,1) distribution and
    assess goodness of fit via binned Pearson's chi^2 test.

    :param numpy.ndarray samples: A vector of real-valued samples from a
        candidate distribution that should be Exponential(1)-distributed.
    :param bool plot: Whether to print a histogram. Defaults to False.
    :returns: Goodness of fit, as a p-value.
    :rtype: float
    """
    samples = jax.lax.stop_gradient(samples)
    unif01_samples = np.exp(-samples)
    return unif01_goodness_of_fit(unif01_samples, plot=plot)


def density_goodness_of_fit(samples, probs, plot=False):
    """
    Transform arbitrary continuous samples to Uniform(0,1) distribution and
    assess goodness of fit via binned Pearson's chi^2 test.

    :param numpy.ndarray samples: A vector list of real-valued samples from a
        distribution.
    :param numpy.ndarray probs: A vector of probability densities evaluated at
        those samples.
    :param bool plot: Whether to print a histogram. Defaults to False.
    :returns: Goodness of fit, as a p-value.
    :rtype: float
    """
    samples = jax.lax.stop_gradient(samples)
    probs = jax.lax.stop_gradient(probs)
    assert samples.shape == probs.shape
    if len(samples) <= 100:
        raise InvalidTest("imprecision; use more samples")

    index = np.argsort(samples, 0, kind="stable")
    samples = samples[index]
    probs = probs[index]
    gaps = samples[1:] - samples[:-1]

    sparsity = 1 / probs
    sparsity = 0.5 * (sparsity[1:] + sparsity[:-1])
    density = len(samples) / sparsity

    exp_samples = density * gaps
    return exp_goodness_of_fit(exp_samples, plot=plot)


def volume_of_sphere(dim, radius):
    return radius ** dim * math.pi ** (0.5 * dim) / math.gamma(0.5 * dim + 1)


def get_nearest_neighbor_distances(samples):
    try:
        # This version scales as O(N log(N)).
        from scipy.spatial import cKDTree

        distances, indices = cKDTree(samples).query(samples, k=2)
        return distances[:, 1]
    except ImportError:
        # This version scales as O(N^2).
        x = samples
        x2 = (x * x).sum(-1)
        d2 = x2[:, None] + x2 - 2 * x @ x.T
        min_d2 = np.partition(d2, 1)[:, 1]
        return np.sqrt(np.clip(min_d2, 0, None))


def vector_density_goodness_of_fit(samples, probs, *, dim=None, plot=False):
    """
    Transform arbitrary multivariate continuous samples to Univariate(0,1)
    distribution via nearest neighbor distribution [1,2,3] and assess goodness
    of fit via binned Pearson's chi^2 test.

    [1] Peter J. Bickel and Leo Breiman (1983)
        "Sums of Functions of Nearest Neighbor Distances, Moment Bounds, Limit
        Theorems and a Goodness of Fit Test"
        https://projecteuclid.org/download/pdf_1/euclid.aop/1176993668
    [2] Mike Williams (2010)
        "How good are your fits? Unbinned multivariate goodness-of-fit tests in
        high energy physics."
        https://arxiv.org/abs/1006.3019
    [3] Nearest Neighbour Distribution
        https://en.wikipedia.org/wiki/Nearest_neighbour_distribution

    :param numpy.ndarray samples: A tensor of real-vector-valued samples from a
        distribution.
    :param numpy.ndarray probs: A vector of probability densities evaluated at
        those samples.
    :param int dim: Optional dimension of the submanifold on which data lie.
        Defaults to ``samples.shape[-1]``.
    :param bool plot: Whether to print a histogram. Defaults to False.
    :returns: Goodness of fit, as a p-value.
    :rtype: float
    """
    samples = jax.lax.stop_gradient(samples)
    probs = jax.lax.stop_gradient(probs)
    assert samples.shape and len(samples)
    assert probs.shape == samples.shape[:1]
    if dim is None:
        dim = samples.shape[-1]
    assert dim
    if len(samples) <= 1000 * dim:
        raise InvalidTest("imprecision; use more samples")
    radii = get_nearest_neighbor_distances(samples)
    density = len(samples) * probs
    volume = volume_of_sphere(dim, radii)
    exp_samples = density * volume
    return exp_goodness_of_fit(exp_samples, plot=plot)


def auto_goodness_of_fit(samples, probs, *, dim=None, plot=False):
    """
    Dispatch on sample dimension and delegate to either
    :func:`density_goodness_of_fit` or :func:`vector_density_goodness_of_fit`.

    :param numpy.ndarray samples: A tensor of samples stacked on their leftmost
        dimension.
    :param numpy.ndarray probs: A vector of probabilities evaluated at those
        samples.
    :param int dim: Optional manifold dimension, defaults to
        ``samples[:1].size``.
    :param bool plot: Whether to print a histogram. Defaults to False.
    """
    samples = jax.lax.stop_gradient(samples)
    probs = jax.lax.stop_gradient(probs)
    assert samples.shape and samples.shape[0]
    assert probs.shape == samples.shape[:1]

    samples = samples.reshape(samples.shape[0], -1)
    ambient_dim = samples[:1].size
    if dim is None:
        dim = ambient_dim

    if ambient_dim == 0:
        return 1.0
    if ambient_dim == 1:
        samples = samples.reshape(-1)
        return density_goodness_of_fit(samples, probs, plot=plot)
    return vector_density_goodness_of_fit(samples, probs, dim=dim, plot=plot)


def _safe_log(x):
    if x > sys.float_info.min:
        value = math.log(x)
    else:
        value = -math.inf
    return value


def _incomplete_gamma(x, s):
    r"""
    This function computes the incomplete lower gamma function
    using the series expansion:

    .. math::

       \gamma(x, s) = x^s \Gamma(s)e^{-x}\sum^\infty_{k=0}
                    \frac{x^k}{\Gamma(s + k + 1)}

    This series will converge strongly because the Gamma
    function grows factorially.

    Because the Gamma function does grow so quickly, we can
    run into numerical stability issues. To solve this we carry
    out as much math as possible in the log domain to reduce
    numerical error. This function matches the results from
    scipy to numerical precision.
    """
    if x < 0:
        return 1
    if x > 1e3:
        return math.gamma(s)
    log_gamma_s = math.lgamma(s)
    log_x = _safe_log(x)
    value = 0
    for k in range(100):
        log_num = (k + s) * log_x + (-x) + log_gamma_s
        log_denom = math.lgamma(k + s + 1)
        value += math.exp(log_num - log_denom)
    return value


def _chi2sf(x, s):
    r"""
    This function returns the survival function of the chi^2
    distribution. The survival function is given as:

    .. math::
       1 - CDF

    where rhe chi^2 CDF is given as

    .. math::
       F(x; s) = \frac{ \gamma( x/2, s/2 ) }{ \Gamma(s/2) },

    with :math:`\gamma` is the incomplete gamma function defined above.
    Therefore, the survival probability is givne by:

    .. math::
       1 - \frac{ \gamma( x/2, s/2 ) }{ \Gamma(s/2) }.

    This function matches the results from
    scipy to numerical precision.
    """
    top = _incomplete_gamma(x / 2, s / 2)
    bottom = math.gamma(s / 2)
    value = top / bottom
    return 1 - value
