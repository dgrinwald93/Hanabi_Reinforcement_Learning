# coding=utf-8
# Copyright 2018 The Dopamine Authors and Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
#
#
# This file is a fork of the original Dopamine code incorporating changes for
# the multiplayer setting and the Hanabi Learning Environment.
#
"""Run methods for training a DQN agent on Atari.

Methods in this module are usually referenced by |train.py|.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import time

from agents import rule_based_agent
from third_party.dopamine import checkpointer
from third_party.dopamine import iteration_statistics
import dqn_agent
import gin.tf
import rl_env
import numpy as np
import rainbow_agent
import tensorflow as tf

LENIENT_SCORE = False


class ObservationStacker(object):
    """Class for stacking agent observations."""

    def __init__(self, history_size, observation_size, num_players):
        """Initializer for observation stacker.

        Args:
          history_size: int, number of time steps to stack.
          observation_size: int, size of observation vector on one time step.
          num_players: int, number of players.
        """
        self._history_size = history_size
        self._observation_size = observation_size
        self._num_players = num_players
        self._obs_stacks = list()
        for _ in range(0, self._num_players):
            self._obs_stacks.append(np.zeros(self._observation_size *
                                             self._history_size))

    def add_observation(self, observation, current_player):
        """Adds observation for the current player.

        Args:
          observation: observation vector for current player.
          current_player: int, current player id.
        """
        self._obs_stacks[current_player] = np.roll(self._obs_stacks[current_player],
                                                   -self._observation_size)
        self._obs_stacks[current_player][(self._history_size - 1) *
                                         self._observation_size:] = observation

    def get_observation_stack(self, current_player):
        """Returns the stacked observation for current player.

        Args:
          current_player: int, current player id.
        """

        return self._obs_stacks[current_player]

    def reset_stack(self):
        """Resets the observation stacks to all zero."""

        for i in range(0, self._num_players):
            self._obs_stacks[i].fill(0.0)

    @property
    def history_size(self):
        """Returns number of steps to stack."""
        return self._history_size

    def observation_size(self):
        """Returns the size of the observation vector after history stacking."""
        return self._observation_size * self._history_size


def load_gin_configs(gin_files, gin_bindings):
    """Loads gin configuration files.

    Args:
      gin_files: A list of paths to the gin configuration files for this
        experiment.
      gin_bindings: List of gin parameter bindings to override the values in the
        config files.
    """
    gin.parse_config_files_and_bindings(gin_files,
                                        bindings=gin_bindings,
                                        skip_unknown=False)


@gin.configurable
def create_environment(game_type='Hanabi-Small', num_players=4):
    """Creates the Hanabi environment.

    Args:
      game_type: Type of game to play. Currently the following are supported:
        Hanabi-Full: Regular game.
        Hanabi-Small: The small version of Hanabi, with 2 cards and 2 colours.
      num_players: Int, number of players to play this game.

    Returns:
      A Hanabi environment.
    """
    return rl_env.make(
        environment_name=game_type, num_players=num_players, pyhanabi_path=None)


@gin.configurable
def create_obs_stacker(environment, history_size=4):
    """Creates an observation stacker.

    Args:
      environment: environment object.
      history_size: int, number of steps to stack.

    Returns:
      An observation stacker object.
    """

    return ObservationStacker(history_size,
                              environment.vectorized_observation_shape()[0],
                              environment.players)


@gin.configurable
def create_agent(environment, obs_stacker, agent_type='DQN'):
    """Creates the Hanabi agent.

    Args:
      environment: The environment.
      obs_stacker: Observation stacker object.
      agent_type: str, type of agent to construct.

    Returns:
      An agent for playing Hanabi.

    Raises:
      ValueError: if an unknown agent type is requested.
    """
    if agent_type == 'DQN':
        return dqn_agent.DQNAgent(observation_size=obs_stacker.observation_size(),
                                  num_actions=environment.num_moves(),
                                  num_players=environment.players)
    elif agent_type == 'Rainbow':
        return rainbow_agent.RainbowAgent(
            observation_size=obs_stacker.observation_size(),
            num_actions=environment.num_moves(),
            num_players=environment.players)
    elif agent_type == "SimpleAgent":
        return rule_based_agent.RuleBasedAgent(
            players=environment.players)
    else:
        raise ValueError('Expected valid agent_type, got {}'.format(agent_type))

@gin.configurable
def create_adhoc_team(environment, obs_stacker, team_no, rl_shared_model=True):
    """

    :param environment:
    :param obs_stacker:
    :param team:
    :return:
    """
    agent_repo = ['DQN', 'SimpleAgent', 'Rainbow']

    if team_no == 1:
        team = {"Rainbow": [0], 'SimpleAgent': [1, 2, 3], }
    elif team_no == 2:
        team = {'Rainbow': [0, 1], 'SimpleAgent': [2, 3]}
    elif team_no == 3:
        team = {'Rainbow': [0, 1, 2], 'SimpleAgent': [3]}
    else:
        print("No valid team number defined!!")
        return None
    # empty list for agents
    # check first if each position is only listed once
    set_pos = set()

    if not all(agent in agent_repo for agent in team.keys()):
        print("Agent doesnt exist!")
        return None

    # quick check if team is defined correctly
    for positions in team.values():
        for pos in positions:
            # check if team is correctly defined, therefore if
            if pos in set_pos or pos not in range(5):
                print("TEAM NOT CORRECTLY DEFINED! CHECK positions")
                return None
            else:
                set_pos.add(pos)

    list_pos = list(set_pos)
    list_pos.sort()
    # check if list is sequential
    if not all(a == b for a, b in enumerate(list_pos)):
        print('Index positions are not sequential')
        return None

    # create a dictionary for team, which will be returned. for each position
    agent_list = [0] * len(list_pos)
    for agent_type in team:

        agent = None
        for pos in team[agent_type]:
            if agent_type == 'DQN':
                if dqn_agent.DQNAgent.is_rl_agent() and rl_shared_model \
                        and agent is not None:
                    agent_list[pos] = agent
                else:
                    agent = dqn_agent.DQNAgent(
                        observation_size=obs_stacker.observation_size(),
                        num_actions=environment.num_moves(),
                        num_players=team_no)
                    agent_list[pos] = agent

            elif agent_type == 'Rainbow':
                if dqn_agent.DQNAgent.is_rl_agent() and rl_shared_model \
                        and agent is not None:
                    agent_list[pos] = agent
                else:
                    agent = rainbow_agent.RainbowAgent(
                        observation_size=obs_stacker.observation_size(),
                        num_actions=environment.num_moves(),
                        num_players=team_no)
                    agent_list[pos] = agent

            elif agent_type == "SimpleAgent":
                if dqn_agent.DQNAgent.is_rl_agent() and rl_shared_model \
                        and agent is not None:
                    agent_list[pos] = agent
                else:
                    agent = rule_based_agent.RuleBasedAgent(
                        players=environment.players)
                    agent_list[pos] = agent
            else:
                raise ValueError('Expected valid agent_type, got {}'.format(agent_type))

    return agent_list


def create_adhoc_training_team(environment, obs_stacker):
    """
    Creates a team of agents used for adhoc-mode training
    :param environment:
    :param obs_stacker:
    :param team_agents: a dictionary of
    :return: a list of agents playing the game
    """
    agents_list = [dqn_agent.DQNAgent(observation_size=obs_stacker.observation_size(),
                                      num_actions=environment.num_moves(),
                                      num_players=environment.players),
                   rule_based_agent.RuleBasedAgent(
                       players=environment.players)]

    return agents_list

def initialize_checkpointing(agent_list, experiment_logger, checkpoint_dir,
                             checkpoint_file_prefix='ckpt'):
    """Reloads the latest checkpoint if it exists.

    The following steps will be taken:
     - This method will first create a Checkpointer object, which will be used in
       the method and then returned to the caller for later use.
     - It will then call checkpointer.get_latest_checkpoint_number to determine
       whether there is a valid checkpoint in checkpoint_dir, and what is the
       largest file number.
     - If a valid checkpoint file is found, it will load the bundled data from
       this file and will pass it to the agent for it to reload its data.
     - If the agent is able to successfully unbundle, this method will verify that
       the unbundled data contains the keys, 'logs' and 'current_iteration'. It
       will then load the Logger's data from the bundle, and will return the
       iteration number keyed by 'current_iteration' as one of the return values
       (along with the Checkpointer object).

    Args:
      agent: The agent that will unbundle the checkpoint from checkpoint_dir.
      experiment_logger: The Logger object that will be loaded from the
        checkpoint.
      checkpoint_dir: str, the directory containing the checkpoints.
      checkpoint_file_prefix: str, the checkpoint file prefix.

    Returns:
      start_iteration: int, The iteration number to start the experiment from.
      experiment_checkpointer: The experiment checkpointer.
    """
    experiment_checkpointer = checkpointer.Checkpointer(
        checkpoint_dir, checkpoint_file_prefix)

    start_iteration = 0

    # Check if checkpoint exists. Note that the existence of checkpoint 0 means
    # that we have finished iteration 0 (so we will start from iteration 1).
    latest_checkpoint_version = checkpointer.get_latest_checkpoint_number(
        checkpoint_dir)
    if latest_checkpoint_version >= 0:
        dqn_dictionary = experiment_checkpointer.load_checkpoint(
            latest_checkpoint_version)
        for agent in agent_list:
            if agent.is_rl_agent():
                if agent.unbundle(
                        checkpoint_dir, latest_checkpoint_version, dqn_dictionary):
                    assert 'logs' in dqn_dictionary
                    assert 'current_iteration' in dqn_dictionary
                    experiment_logger.data = dqn_dictionary['logs']
                    start_iteration = dqn_dictionary['current_iteration'] + 1
                    tf.logging.info('Reloaded checkpoint and will start from iteration %d',
                                    start_iteration)

    return start_iteration, experiment_checkpointer


def format_legal_moves(legal_moves, action_dim):
    """Returns formatted legal moves.

    This function takes a list of actions and converts it into a fixed size vector
    of size action_dim. If an action is legal, its position is set to 0 and -Inf
    otherwise.
    Ex: legal_moves = [0, 1, 3], action_dim = 5
        returns [0, 0, -Inf, 0, -Inf]

    Args:
      legal_moves: list of legal actions.
      action_dim: int, number of actions.

    Returns:
      a vector of size action_dim.
    """
    new_legal_moves = np.full(action_dim, -float('inf'))
    if legal_moves:
        new_legal_moves[legal_moves] = 0
    return new_legal_moves


def parse_observations(observations, num_actions, obs_stacker):
    """Deconstructs the rich observation data into relevant components.

    Args:
      observations: dict, containing full observations.
      num_actions: int, The number of available actions.
      obs_stacker: Observation stacker object.

    Returns:
      current_player: int, Whose turn it is.
      legal_moves: `np.array` of floats, of length num_actions, whose elements
        are -inf for indices corresponding to illegal moves and 0, for those
        corresponding to legal moves.
      observation_vector: Vectorized observation for the current player.
    """
    current_player = observations['current_player']
    current_player_observation = (
        observations['player_observations'][current_player])

    legal_moves = current_player_observation['legal_moves_as_int']
    legal_moves = format_legal_moves(legal_moves, num_actions)

    observation_vector = current_player_observation['vectorized']
    obs_stacker.add_observation(observation_vector, current_player)
    observation_vector = obs_stacker.get_observation_stack(current_player)

    return current_player, legal_moves, observation_vector


def parse_player_observation(observations):
    current_player = observations['current_player']

    return observations['player_observations'][current_player]


def run_one_episode(agents_list, environment, obs_stacker):
    """Runs the agent on a single game of Hanabi in adhoc-play mode.

    Args:
      agents: Agents playing Hanabi.
      environment: The Hanabi environment.
      obs_stacker: Observation stacker object.

    Returns:
      step_number: int, number of actions in this episode.
      total_reward: float, undiscounted return for this episode.
    """
    obs_stacker.reset_stack()
    observations = environment.reset()
    current_player, legal_moves, observation_vector = (
        parse_observations(observations, environment.num_moves(), obs_stacker))

    if agents_list[current_player].is_rl_agent():
        action = agents_list[current_player].begin_episode(current_player, legal_moves,
                                                           observation_vector)
    else:
        # print("Observations: ", observations)
        player_observation = parse_player_observation(observations)
        action = agents_list[current_player].act_train(player_observation)
        # print(action)

    #print("First action is: ", type(action))

    is_done = False
    total_reward = 0
    step_number = 0

    has_played = {current_player}

    # Keep track of per-player reward.
    reward_since_last_action = np.zeros(environment.players)

    while not is_done:
        observations, reward, is_done, info = environment.step(action.item())

        modified_reward = max(reward, 0) if LENIENT_SCORE else reward
        total_reward += modified_reward

        should_get_rewarded = info['should_get_rewarded']

        if should_get_rewarded is not None:

            for turns_since, player_fix, reward_fix in should_get_rewarded:

                for idx, agent in enumerate(agents_list):

                    if agent.is_rl_agent() and idx==player_fix:

                        print("Will give agent at pos{}, reward of {}, for move at:{}"
                              .format(player_fix, reward_fix, turns_since))
                        agent.reset_transition(player_fix, turns_since, reward_fix)

        reward_since_last_action += modified_reward

        step_number += 1
        if is_done:
            break
        current_player, legal_moves, observation_vector = (
            parse_observations(observations, environment.num_moves(), obs_stacker))

        # print("Agent: {} is making an action.".format(agents_list[current_player]))
        if current_player in has_played:
            if agents_list[current_player].is_rl_agent():
                action = agents_list[current_player].step(reward_since_last_action[current_player],
                                                 current_player, legal_moves, observation_vector)
            else:
                player_observation = parse_player_observation(observations)
                action = agents_list[current_player].act_train(player_observation)
        else:

            if agents_list[current_player].is_rl_agent():
                action = agents_list[current_player].begin_episode(current_player, legal_moves,
                                                                   observation_vector)
            else:
                player_observation = parse_player_observation(observations)
                action = agents_list[current_player].act_train(player_observation)

            has_played.add(current_player)

        # Reset this player's reward accumulator.
        reward_since_last_action[current_player] = 0
    for agent in agents_list:
        if agent.is_rl_agent():
            agent.end_episode(reward_since_last_action)

    tf.logging.info('EPISODE: steps:%d total_reward:%g', step_number, total_reward)
    return step_number, total_reward


def run_one_phase(agents_list, environment, obs_stacker, min_steps, statistics,
                  run_mode_str):
    """Runs the agent/environment loop until a desired number of steps.

    Args:
      agent: Agent playing hanabi.
      environment: environment object.
      obs_stacker: Observation stacker object.
      min_steps: int, minimum number of steps to generate in this phase.
      statistics: `IterationStatistics` object which records the experimental
        results.
      run_mode_str: str, describes the run mode for this agent.

    Returns:
      The number of steps taken in this phase, the sum of returns, and the
        number of episodes performed.
    """
    step_count = 0
    num_episodes = 0
    sum_returns = 0.

    while step_count < min_steps:
        episode_length, episode_return = run_one_episode(agents_list, environment,
                                                         obs_stacker)
        statistics.append({
            '{}_episode_lengths'.format(run_mode_str): episode_length,
            '{}_episode_returns'.format(run_mode_str): episode_return
        })

        step_count += episode_length
        sum_returns += episode_return
        num_episodes += 1

    return step_count, sum_returns, num_episodes


@gin.configurable
def run_one_iteration(agents_list, environment, obs_stacker,
                      iteration, training_steps,
                      evaluate_every_n=100,
                      num_evaluation_games=100):
    """Runs one iteration of agent/environment interaction.

    An iteration involves running several episodes until a certain number of
    steps are obtained.

    Args:
      agent: Agent playing hanabi.
      environment: The Hanabi environment.
      obs_stacker: Observation stacker object.
      iteration: int, current iteration number, used as a global_step.
      training_steps: int, the number of training steps to perform.
      evaluate_every_n: int, frequency of evaluation.
      num_evaluation_games: int, number of games per evaluation.

    Returns:
      A dict containing summary statistics for this iteration.
    """
    start_time = time.time()

    statistics = iteration_statistics.IterationStatistics()

    # First perform the training phase, during which the agent learns.
    for agent in agents_list:
        if agent.is_rl_agent():
            agent.eval_mode = False

    number_steps, sum_returns, num_episodes = (
        run_one_phase(agents_list, environment, obs_stacker, training_steps, statistics,
                      'train'))
    time_delta = time.time() - start_time
    tf.logging.info('Average training steps per second: %.2f',
                    number_steps / time_delta)

    average_return = sum_returns / num_episodes
    tf.logging.info('Average per episode return: %.2f', average_return)
    statistics.append({'average_return': average_return})

    # Also run an evaluation phase if desired.
    if evaluate_every_n is not None and iteration % evaluate_every_n == 0:
        episode_data = []
        for agent in agents_list:
            if agent.is_rl_agent():
                agent.eval_mode = True
        # Collect episode data for all games.
        for _ in range(num_evaluation_games):
            episode_data.append(run_one_episode(agents_list, environment, obs_stacker))

        eval_episode_length, eval_episode_return = map(np.mean, zip(*episode_data))

        statistics.append({
            'eval_episode_lengths': eval_episode_length,
            'eval_episode_returns': eval_episode_return
        })
        tf.logging.info('Average eval. episode length: %.2f  Return: %.2f',
                        eval_episode_length, eval_episode_return)
    else:
        statistics.append({
            'eval_episode_lengths': -1,
            'eval_episode_returns': -1
        })

    return statistics.data_lists


def log_experiment(experiment_logger, iteration, statistics,
                   logging_file_prefix='log', log_every_n=1):
    """Records the results of the current iteration.

    Args:
      experiment_logger: A `Logger` object.
      iteration: int, iteration number.
      statistics: Object containing statistics to log.
      logging_file_prefix: str, prefix to use for the log files.
      log_every_n: int, specifies logging frequency.
    """
    if iteration % log_every_n == 0:
        experiment_logger['iter{:d}'.format(iteration)] = statistics
        experiment_logger.log_to_file(logging_file_prefix, iteration)


def checkpoint_experiment(experiment_checkpointer, agents_list, experiment_logger,
                          iteration, checkpoint_dir, checkpoint_every_n):
    """Checkpoint experiment data.

    Args:
      experiment_checkpointer: A `Checkpointer` object.
      agent: An RL agent.
      experiment_logger: a Logger object, to include its data in the checkpoint.
      iteration: int, iteration number for checkpointing.
      checkpoint_dir: str, the directory where to save checkpoints.
      checkpoint_every_n: int, the frequency for writing checkpoints.
    """
    if iteration % checkpoint_every_n == 0:

        for agent in agents_list:
            if agent.is_rl_agent():
                agent_dictionary = agent.bundle_and_checkpoint(checkpoint_dir, iteration)
                if agent_dictionary:
                    agent_dictionary['current_iteration'] = iteration
                    agent_dictionary['logs'] = experiment_logger.data
                    experiment_checkpointer.save_checkpoint(iteration, agent_dictionary)


@gin.configurable
def run_experiment(agents_list,
                   environment,
                   start_iteration,
                   obs_stacker,
                   experiment_logger,
                   experiment_checkpointer,
                   checkpoint_dir,
                   num_iterations=200,
                   training_steps=5000,
                   logging_file_prefix='log',
                   log_every_n=1,
                   checkpoint_every_n=1):
    """Runs a full experiment, spread over multiple iterations."""
    tf.logging.info('Beginning training...')
    if num_iterations <= start_iteration:
        tf.logging.warning('num_iterations (%d) < start_iteration(%d)',
                           num_iterations, start_iteration)
        return

    for iteration in range(start_iteration, num_iterations):
        start_time = time.time()
        statistics = run_one_iteration(agents_list, environment, obs_stacker, iteration,
                                       training_steps)
        tf.logging.info('Iteration %d took %d seconds', iteration,
                        time.time() - start_time)
        start_time = time.time()
        log_experiment(experiment_logger, iteration, statistics,
                       logging_file_prefix, log_every_n)
        tf.logging.info('Logging iteration %d took %d seconds', iteration,
                        time.time() - start_time)
        start_time = time.time()
        checkpoint_experiment(experiment_checkpointer, agents_list, experiment_logger,
                              iteration, checkpoint_dir, checkpoint_every_n)
        tf.logging.info('Checkpointing iteration %d took %d seconds', iteration,
                        time.time() - start_time)
