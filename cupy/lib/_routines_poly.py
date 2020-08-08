import functools
import warnings

import numpy

import cupy


def _wraps_polyroutine(func):
    def _get_coeffs(x):
        if isinstance(x, cupy.poly1d):
            return x._coeffs
        if cupy.isscalar(x):
            return cupy.atleast_1d(x)
        if isinstance(x, cupy.ndarray):
            x = cupy.atleast_1d(x)
            if x.ndim == 1:
                return x
            raise ValueError('Multidimensional inputs are not supported')
        raise TypeError('Unsupported type')

    def wrapper(*args):
        coeffs = [_get_coeffs(x) for x in args]
        out = func(*coeffs)

        if all([not isinstance(x, cupy.poly1d) for x in args]):
            return out
        if isinstance(out, cupy.ndarray):
            return cupy.poly1d(out)
        if isinstance(out, tuple):
            return tuple([cupy.poly1d(x) for x in out])
        assert False  # Never reach

    return functools.update_wrapper(wrapper, func)


@_wraps_polyroutine
def polyadd(a1, a2):
    """Computes the sum of two polynomials.

    Args:
        a1 (scalar, cupy.ndarray or cupy.poly1d): first input polynomial.
        a2 (scalar, cupy.ndarray or cupy.poly1d): second input polynomial.

    Returns:
        cupy.ndarray or cupy.poly1d: The sum of the inputs.

    .. seealso:: :func:`numpy.polyadd`

    """
    if a1.size < a2.size:
        a1, a2 = a2, a1
    out = cupy.pad(a2, (a1.size - a2.size, 0))
    out = out.astype(cupy.result_type(a1, a2), copy=False)
    out += a1
    return out


@_wraps_polyroutine
def polysub(a1, a2):
    """Computes the difference of two polynomials.

    Args:
        a1 (scalar, cupy.ndarray or cupy.poly1d): first input polynomial.
        a2 (scalar, cupy.ndarray or cupy.poly1d): second input polynomial.

    Returns:
        cupy.ndarray or cupy.poly1d: The difference of the inputs.

    .. seealso:: :func:`numpy.polysub`

    """
    if a1.shape[0] <= a2.shape[0]:
        out = cupy.pad(a1, (a2.shape[0] - a1.shape[0], 0))
        out = out.astype(cupy.result_type(a1, a2), copy=False)
        out -= a2
    else:
        out = cupy.pad(a2, (a1.shape[0] - a2.shape[0], 0))
        out = out.astype(cupy.result_type(a1, a2), copy=False)
        out -= 2 * out - a1
    return out


@_wraps_polyroutine
def polymul(a1, a2):
    """Computes the product of two polynomials.

    Args:
        a1 (scalar, cupy.ndarray or cupy.poly1d): first input polynomial.
        a2 (scalar, cupy.ndarray or cupy.poly1d): second input polynomial.

    Returns:
        cupy.ndarray or cupy.poly1d: The product of the inputs.

    .. seealso:: :func:`numpy.polymul`

    """
    a1 = cupy.trim_zeros(a1, trim='f')
    a2 = cupy.trim_zeros(a2, trim='f')
    if a1.size == 0:
        a1 = cupy.array([0.])
    if a2.size == 0:
        a2 = cupy.array([0.])
    return cupy.convolve(a1, a2)


def _astype(x):
    if x.dtype.kind == 'c':
        return x.astype(numpy.complex128, copy=False)
    return x.astype(numpy.float64, copy=False)


def polyfit(x, y, deg, rcond=None, full=False, w=None, cov=False):
    """Returns the least squares fit of polynomial of degree deg
    to the data y sampled at x.

    Args:
        x (cupy.ndarray or cupy.poly1d): x-coordinates of the sample
            points of shape (M, ).
        y (cupy.ndarray or cupy.poly1d): y-coordinates of the sample
            points of shape (M, ) or (M, K).
        deg (int): degree of the fitting polynomial.
        rcond (float, optional): relative condition number of the fit.
            The default value is len(x) * eps.
        full (bool, optional): indicator of the return value nature.
            When False (default), only the coefficients are returned.
            When True, diagnostic information is also returned.
        w (cupy.ndarray, optional): weights applied to the y-coordinates
            of the sample points of shape (M, ).
        cov (bool or str, optional): if given, returns the coefficients
            along with the covariance matrix.

    Returns:
        cupy.ndarray: of shape (deg + 1,) or (deg + 1, K).
            Polynomial coefficients from highest to lowest degree
        tuple (cupy.ndarray, int, cupy.ndarray, float):
            Present only if ``full`` = True.
            Sum of squared residuals of the least-squares fit,
            rank of the scaled Vandermonde coefficient matrix,
            its singular values, and the specified value of ``rcond``.
        cupy.ndarray: of shape (M, M) or (M, M, K).
            Present only if ``full`` = False and ``cov`` = True.
            The covariance matrix of the polynomial coefficient estimates.

    .. warning::

        numpy.RankWarning: The rank of the coefficient matrix in the
        least-squares fit is deficient. It is raised if ``full`` = False.

    .. seealso:: :func:`numpy.polyfit`

    """
    if isinstance(x, cupy.poly1d):
        x = x.coeffs
    if isinstance(y, cupy.poly1d):
        y = y.coeffs

    x = _astype(x)
    y = _astype(y)
    deg = int(deg)

    if deg < 0:
        raise ValueError('expected deg >= 0')
    if x.ndim != 1:
        raise TypeError('expected 1D vector for x')
    if x.size == 0:
        raise TypeError('expected non-empty vector for x')
    if y.ndim < 1 or y.ndim > 2:
        raise TypeError('expected 1D or 2D array for y')
    if x.size != y.shape[0]:
        raise TypeError('expected x and y to have same length')

    lhs = cupy.polynomial.polynomial.polyvander(x, deg)[:, ::-1]
    rhs = y

    if w is not None:
        w = _astype(w)
        if w.ndim != 1:
            raise TypeError('expected a 1-d array for weights')
        if w.size != x.size:
            raise TypeError('expected w and y to have the same length')

        lhs *= w[:, None]
        if rhs.ndim == 2:
            w = w[:, None]
        rhs *= w

    if rcond is None:
        rcond = x.size * cupy.finfo(x.dtype).eps

    scale = cupy.sqrt((cupy.square(lhs)).sum(axis=0))
    lhs /= scale
    c, resids, rank, s = cupy.linalg.lstsq(lhs, rhs, rcond)
    c = (c.T / scale).T

    order = deg + 1
    if rank != order and not full:
        msg = 'Polyfit may be poorly conditioned'
        warnings.warn(msg, numpy.RankWarning, stacklevel=4)

    if y.ndim != 1:
        c = cupy.ascontiguousarray(c)

    if full:
        return c, resids, rank, s, rcond
    if cov:
        base = cupy.linalg.inv(cupy.dot(lhs.T, lhs))
        base /= cupy.outer(scale, scale)

        if cov == 'unscaled':
            factor = 1
        elif x.size > order:
            factor = resids / (x.size - order)
        else:
            raise ValueError('the number of data points must exceed order'
                             ' to scale the covariance matrix')

        if y.ndim != 1:
            base = base[..., None]
        return c, base * factor

    return c


def roots(p):
    """Computes the roots of a polynomial with given coefficients.

    Args:
        p (cupy.ndarray or cupy.poly1d): polynomial coefficients.

    Returns:
        cupy.ndarray: polynomial roots.

    .. warning::

        This function doesn't support currently polynomial coefficients
        whose companion matrices are general 2d square arrays. Only those
        with complex Hermitian or real symmetric 2d arrays are allowed.

        The current `cupy.roots` doesn't guarantee the order of results.

    .. seealso:: :func:`numpy.roots`

    """
    if isinstance(p, cupy.poly1d):
        p = p.coeffs
    if p.dtype.kind == 'b':
        raise NotImplementedError('boolean inputs are not supported')
    if p.ndim == 0:
        raise TypeError('0-dimensional input is not allowed')
    if p.size < 2:
        return cupy.array([])
    [p] = cupy.polynomial.polyutils.as_series([p[::-1]])
    if p.size < 2:
        return cupy.array([])
    if p.size == 2:
        out = (-p[0] / p[1])[None]
        if p[0] == 0:
            out = out.real.astype(numpy.float64)
        return out
    cmatrix = cupy.polynomial.polynomial.polycompanion(p)
    # TODO(Dahlia-Chehata): Support after cupy.linalg.eigvals is supported
    if cupy.array_equal(cmatrix, cmatrix.conj().T):
        out = cupy.linalg.eigvalsh(cmatrix)
    else:
        raise NotImplementedError('Only complex Hermitian and real '
                                  'symmetric 2d arrays are supported '
                                  'currently')
    return out.astype(p.dtype)
