# -*- coding: utf-8 -*-
# File: pool.py

import numpy as np
from ..compat import tfv1 as tf  # this should be avoided first in model code

from ..utils.argtools import get_data_format, shape2d
from .common import layer_register
from .shape_utils import StaticDynamicShape
from .tflayer import convert_to_tflayer_args

__all__ = ['MaxPooling', 'FixedUnPooling', 'AvgPooling', 'GlobalAvgPooling']


@layer_register(log_shape=True)
@convert_to_tflayer_args(
    args_names=['pool_size', 'strides'],
    name_mapping={'shape': 'pool_size', 'stride': 'strides'})
def MaxPooling(
        inputs,
        pool_size,
        strides=None,
        padding='valid',
        data_format='channels_last'):
    """
    Same as `tf.layers.MaxPooling2D`. Default strides is equal to pool_size.
    """
    if strides is None:
        strides = pool_size
    layer = tf.layers.MaxPooling2D(pool_size, strides, padding=padding, data_format=data_format)
    ret = layer.apply(inputs, scope=tf.get_variable_scope())
    return tf.identity(ret, name='output')


@layer_register(log_shape=True)
@convert_to_tflayer_args(
    args_names=['pool_size', 'strides'],
    name_mapping={'shape': 'pool_size', 'stride': 'strides'})
def AvgPooling(
        inputs,
        pool_size,
        strides=None,
        padding='valid',
        data_format='channels_last'):
    """
    Same as `tf.layers.AveragePooling2D`. Default strides is equal to pool_size.
    """
    if strides is None:
        strides = pool_size
    layer = tf.layers.AveragePooling2D(pool_size, strides, padding=padding, data_format=data_format)
    ret = layer.apply(inputs, scope=tf.get_variable_scope())
    return tf.identity(ret, name='output')


@layer_register(log_shape=True)
def GlobalAvgPooling(x, data_format='channels_last'):
    """
    Global average pooling as in the paper `Network In Network
    <http://arxiv.org/abs/1312.4400>`_.

    Args:
        x (tf.Tensor): a 4D tensor.

    Returns:
        tf.Tensor: a NC tensor named ``output``.
    """
    assert x.shape.ndims == 4
    data_format = get_data_format(data_format)
    axis = [1, 2] if data_format == 'channels_last' else [2, 3]
    return tf.reduce_mean(x, axis, name='output')


def UnPooling2x2ZeroFilled(x):
    # https://github.com/tensorflow/tensorflow/issues/2169
    out = tf.concat([x, tf.zeros_like(x)], 3)
    out = tf.concat([out, tf.zeros_like(out)], 2)

    sh = x.get_shape().as_list()
    if None not in sh[1:]:
        out_size = [-1, sh[1] * 2, sh[2] * 2, sh[3]]
        return tf.reshape(out, out_size)
    else:
        shv = tf.shape(x)
        ret = tf.reshape(out, tf.stack([-1, shv[1] * 2, shv[2] * 2, sh[3]]))
        return ret


@layer_register(log_shape=True)
def FixedUnPooling(x, shape, unpool_mat=None, data_format='channels_last'):
    N = tf.shape(x)[0]
    H = tf.shape(x)[1]
    W = tf.shape(x)[2]
    C = tf.shape(x)[3]
    return tf.image.resize(x, [2*H, 2*W], 'nearest')
    
def FixedUnPooling_org(x, shape, unpool_mat=None, data_format='channels_last'):
    """
    Unpool the input with a fixed matrix to perform kronecker product with.

    Args:
        x (tf.Tensor): a 4D image tensor
        shape: int or (h, w) tuple
        unpool_mat: a tf.Tensor or np.ndarray 2D matrix with size=shape.
            If is None, will use a matrix with 1 at top-left corner.

    Returns:
        tf.Tensor: a 4D image tensor.
    """
    data_format = get_data_format(data_format, keras_mode=False)
    shape = shape2d(shape)

    output_shape = StaticDynamicShape(x)
    output_shape.apply(1 if data_format == 'NHWC' else 2, lambda x: x * shape[0])
    output_shape.apply(2 if data_format == 'NHWC' else 3, lambda x: x * shape[1])

    # a faster implementation for this special case
    if shape[0] == 2 and shape[1] == 2 and unpool_mat is None and data_format == 'NHWC':
        ret = UnPooling2x2ZeroFilled(x)
    else:
        # check unpool_mat
        if unpool_mat is None:
            mat = np.zeros(shape, dtype='float32')
            mat[0][0] = 1
            unpool_mat = tf.constant(mat, name='unpool_mat')
        elif isinstance(unpool_mat, np.ndarray):
            unpool_mat = tf.constant(unpool_mat, name='unpool_mat')
        assert unpool_mat.shape.as_list() == list(shape)

        if data_format == 'NHWC':
            x = tf.transpose(x, [0, 3, 1, 2])

        # till this point FixedUnPooling_fix is same as FixedUnPooling.

        # perform a tensor-matrix kronecker product
        N = tf.shape(x)[0]
        C = tf.shape(x)[1]
        H = tf.shape(x)[2]
        W = tf.shape(x)[3]
        SH = shape[0]
        SW = shape[1]
        x = tf.expand_dims(x, -1)       # bchwx1
        mat = tf.expand_dims(unpool_mat, 0)  # 1xshxsw

        #pdb.set_trace()
        ret = tf.tensordot(x, mat, axes=1)  # bxcxhxwxshxsw

        ret = tf.reshape(ret, [N*C, H, W, shape[0], shape[1]]) #b*c x h x w x sh x sw

        '''
        if data_format == 'NHWC':
            ret = tf.transpose(ret, [0, 2, 4, 3, 5, 1])
        else:
            ret = tf.transpose(ret, [0, 1, 2, 4, 3, 5])
        '''

        ret = tf.transpose(ret, [0, 1, 3, 2, 4]) # b*c x h x sh x w x sw

        ret = tf.reshape(ret, tf.stack([N, C, H*SH, W*SW])) # b x c x h*sh x w*sw

        if data_format == 'NHWC':
            ret = tf.transpose(ret, [0, 2, 3, 1]) # earlier just before the tensordot we had turned NHWC to NCHW. So return it to NHWC

        #shape3_dyn = [output_shape.get_dynamic(k) for k in range(1, 4)]
        #ret = tf.reshape(ret, tf.stack([-1] + shape3_dyn))

    #pdb.set_trace()
    ret.set_shape(tf.TensorShape(output_shape.get_static()))
    return ret

