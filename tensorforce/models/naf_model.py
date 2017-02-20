# Copyright 2016 reinforce.io. All Rights Reserved.
# ==============================================================================

"""
Implements normalized advantage functions, largely following

https://github.com/carpedm20/NAF-tensorflow/blob/master/src/network.py

for the update logic with different modularisation.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import tensorflow as tf
from six.moves import xrange
from tensorflow.contrib.framework import get_variables

from tensorforce.models import Model
from tensorforce.models.neural_networks import NeuralNetwork
from tensorforce.models.neural_networks.layers import linear
from tensorforce.util.experiment_util import global_seed

from tensorforce.default_configs import NAFModelConfig

class NAFModel(Model):
    default_config = NAFModelConfig

    def __init__(self, config, scope):
        """
        Training logic for NAFs.

        :param config: Configuration parameters
        """
        super(NAFModel, self).__init__(config, scope)
        self.action_count = self.config.actions
        self.tau = self.config.tau
        self.epsilon = self.config.epsilon
        self.gamma = self.config.gamma
        self.batch_size = self.config.batch_size

        if self.config.deterministic_mode:
            self.random = global_seed()
        else:
            self.random = np.random.RandomState()

        self.state = tf.placeholder(tf.float32, self.batch_shape + list(self.config.state_shape), name="state")
        self.next_states = tf.placeholder(tf.float32, self.batch_shape + list(self.config.state_shape),
                                          name="next_states")

        self.actions = tf.placeholder(tf.float32, [None, self.action_count], name='actions')
        self.terminals = tf.placeholder(tf.float32, [None], name='terminals')
        self.rewards = tf.placeholder(tf.float32, [None], name='rewards')
        self.q_targets = tf.placeholder(tf.float32, [None], name='q_targets')
        self.target_network_update = []
        self.episode = 0

        # Get hidden layers from network generator, then add NAF outputs, same for target network
        scope = '' if self.config.tf_scope is None else self.config.tf_scope + '-'
        self.training_model = NeuralNetwork(self.config.network_layers, self.state, scope=scope + 'training')
        self.target_model = NeuralNetwork(self.config.network_layers, self.next_states, scope=scope + 'target')

        # Create output fields
        self.training_v, self.mu, self.advantage, self.q, self.training_output_vars = self.create_outputs(
            self.training_model.get_output(), 'outputs_training')
        self.target_v, _, _, _, self.target_output_vars = self.create_outputs(self.target_model.get_output(),
                                                                              'outputs_target')
        self.create_training_operations()
        self.saver = tf.train.Saver()
        self.session.run(tf.global_variables_initializer())

    def get_action(self, state, episode=1):
        """
        Returns naf action(s) as given by the mean output of the network.

        :param state: Current state
        :param episode: Current episode
        :param total_states: Total states processed
        :return:
        """
        action = self.session.run(self.mu, {self.state: [state]})[0] + self.exploration(episode, self.total_states)
        self.total_states += 1

        return action

    def update(self, batch):
        """
        Executes a NAF update on a training batch.

        :param batch:=
        :return:
        """
        float_terminals = batch['terminals'].astype(float)

        q_targets = batch['rewards'] + (1. - float_terminals) * self.gamma * np.squeeze(
            self.get_target_value_estimate(batch['next_states']))

        self.session.run([self.optimize_op, self.loss, self.training_v, self.advantage, self.q], {
            self.q_targets: q_targets,
            self.actions: batch['actions'],
            self.state: batch['states']})

    def create_outputs(self, last_hidden_layer, scope):
        """
        Creates NAF specific outputs.

        :param last_hidden_layer: Points to last hidden layer
        :param scope: TF name scope

        :return Output variables and all TF variables created in this scope
        """

        with tf.name_scope(scope):
            # State-value function
            v = linear(last_hidden_layer, {'num_outputs': 1, 'weights_regularizer': self.config.weights_regularizer,
                                           'weights_regularizer_args': [self.config.weights_regularizer_args]}, scope + 'v')

            # Action outputs
            mu = linear(last_hidden_layer, {'num_outputs': self.action_count, 'weights_regularizer': self.config.weights_regularizer,
                                            'weights_regularizer_args': [self.config.weights_regularizer_args]}, scope + 'mu')

            # Advantage computation
            # Network outputs entries of lower triangular matrix L
            lower_triangular_size = int(self.action_count * (self.action_count + 1) / 2)
            l_entries = linear(last_hidden_layer, {'num_outputs': lower_triangular_size,
                                                   'weights_regularizer': self.config.weights_regularizer,
                                                   'weights_regularizer_args': [self.config.weights_regularizer_args]},
                               scope + 'l')

            # Iteratively construct matrix. Extra verbose comment here
            l_rows = []
            offset = 0

            for i in xrange(self.action_count):
                # Diagonal elements are exponentiated, otherwise gradient often 0
                # Slice out lower triangular entries from flat representation through moving offset

                diagonal = tf.exp(tf.slice(l_entries, (0, offset), (-1, 1)))

                n = self.action_count - i - 1
                # Slice out non-zero non-diagonal entries, - 1 because we already took the diagonal
                non_diagonal = tf.slice(l_entries, (0, offset + 1), (-1, n))

                # Fill up row with zeros
                row = tf.pad(tf.concat(1, (diagonal, non_diagonal)), ((0, 0), (i, 0)))
                offset += (self.action_count - i)
                l_rows.append(row)

            # Stack rows to matrix
            l_matrix = tf.transpose(tf.pack(l_rows, axis=1), (0, 2, 1))

            # P = LL^T
            p_matrix = tf.batch_matmul(l_matrix, tf.transpose(l_matrix, (0, 2, 1)))

            # Need to adjust dimensions to multiply with P.
            action_diff = tf.expand_dims(self.actions - mu, -1)

            # A = -0.5 (a - mu)P(a - mu)
            advantage = -0.5 * tf.batch_matmul(tf.transpose(action_diff, [0, 2, 1]),
                                               tf.batch_matmul(p_matrix, action_diff))
            advantage = tf.reshape(advantage, [-1, 1])

            with tf.name_scope('q_values'):
                # Q = A + V
                q_value = v + advantage

        # Get all variables under this scope for target network update
        return v, mu, advantage, q_value, get_variables(scope)

    def create_training_operations(self):
        """
        NAF update logic.
        """

        with tf.name_scope("update"):
            # MSE
            self.loss = tf.reduce_mean(tf.squared_difference(self.q_targets, tf.squeeze(self.q)),
                                       name='loss')
            self.optimize_op = self.optimizer.minimize(self.loss)

        with tf.name_scope("update_target"):
            # Combine hidden layer variables and output layer variables
            self.training_vars = self.training_model.get_variables() + self.training_output_vars
            self.target_vars = self.target_model.get_variables() + self.target_output_vars

            for v_source, v_target in zip(self.training_vars, self.target_vars):
                update = v_target.assign_sub(self.tau * (v_target - v_source))

                self.target_network_update.append(update)

    def get_target_value_estimate(self, next_states):
        """
        Estimate of next state V value through target network.

        :param next_states:
        :return:
        """

        return self.session.run(self.target_v, {self.next_states: next_states})

    def update_target_network(self):
        """
        Updates target network.

        :return:
        """
        self.session.run(self.target_network_update)
