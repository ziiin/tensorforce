# Copyright 2016 reinforce.io. All Rights Reserved.
# ==============================================================================

"""
Preprocessor base class
"""

from __future__ import absolute_import
from __future__ import print_function
from __future__ import division

from tensorforce.config import create_config

class Preprocessor(object):

    # dict containing default configuration
    default_config = {}

    # list specifying order of *args to be parsed
    config_args = []

    def __init__(self, *args, **kwargs):
        """
        Initialize configuration using the default config. Then update the config first using *args (order is
        defined in self.config_args) and then using **kwargs)

        :param args: optional *args
        :param kwargs: optional **kwargs
        """
        self.config = create_config([], default=self.default_config)

        for i, arg in enumerate(args):
            if i >= len(self.config_args):
                break
            self.config.update({self.config_args[i]: arg})

        self.config.update(kwargs)


    def process(self, state):
        """
        Process state.

        :param state: ndarray
        :return: new_state
        """
        return state

    def shape(self, original_shape):
        """
        Return shape of processed state given original shape

        :param original_shape: original shape array
        :return: new shape array
        """
        return original_shape
