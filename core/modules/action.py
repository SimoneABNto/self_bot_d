from asyncio import sleep
from core.modules.scheduler import Scheduler
from core.utils.log import Logger
from random import randrange, choices
from typing import Tuple, List


# get the action trigger
def get_trigger(action_raw):
    return action_raw.get('trigger')


# get the action trigger
def get_channels(action_raw):
    return action_raw.get('channels')


class _ActionData:
    """
    Create an object with all the data in the raw dict
    this represent an action
    """
    class SequenceItem:

        def __init__(self, sequence_item_raw):
            self.message: str = sequence_item_raw.get('message')
            self.execution_probability: int = sequence_item_raw.get('execution_probability', 1)

    def __init__(self, action_raw):
        self._action_raw = action_raw

        # word that will activate this command
        self.trigger: str = action_raw.get('trigger')

        # channels where the messages will be sent
        self.channels: list = action_raw.get('channels')

        # This action will not be affected by pause and resume
        self.run_always = action_raw.get('run_always', False)

        # loop_time: time of the full action loop,
        # loop_range_time: is to avoid execution pragmatically,
        # start_range_time: is the fist execution times
        self.loop_time: int = action_raw.get('loop_time', 1800)
        self.loop_range_time: Tuple[int, int] = tuple(action_raw.get('loop_range_time', (120, 600)))
        self.start_range_time: Tuple[int, int] = tuple(action_raw.get('start_range_time', (20, 60)))

        # sometimes the command wont be executed range(0, 1) float probability
        self.execution_probability: int = action_raw.get('execution_probability', 1)

        # each action can have multiple actions, with unique properties
        self.sequence: List[_ActionData.SequenceItem] = []
        sequences_raw = action_raw.get('sequence', [])

        for sequence_item_raw in sequences_raw:
            self.sequence.append(_ActionData.SequenceItem(sequence_item_raw))


class _ActionDescriptor:
    """
    Data of the action that have to be executed
    """
    def __init__(self, total_time, typing_time, message):
        self.total_time = total_time
        self.typing_time = typing_time
        self.message = message

    def __repr__(self):
        return f'(sleep:{self.total_time}, type:{self.typing_time}, mex:{self.message})'


class _ActionExecutor:

    def __init__(self, action_data: _ActionData, logger):
        self.data: _ActionData = action_data

        self.logger: Logger = logger

        self.__stop_action = False
        self.__pause_action = False

    def _generate_time_action(self, is_first=False) -> List[_ActionDescriptor]:
        """
        :param is_first: if this is the fist time for this action that the generate_time_action is called

        All the actions are part of the total loop time.

        The time of this action should not exceed the total loop time.
        If you exceed the loop time the loop will become more longer then expected.
        """
        time_actions = []
        total_time = 0

        def do_action(probability):
            return choices([True, False], [probability, 1 - probability])[0]

        # this action dont have to be executed on this loop, return a waiting action
        if not do_action(self.data.execution_probability):
            return [_ActionDescriptor(self.data.loop_time, 0, '')]

        # on the first call the loop will kick in faster or slower
        # based on the start_range_time, all others loops will be calculated on loop_range_time
        if is_first:
            range_min, range_max = self.data.start_range_time
        else:
            range_min, range_max = self.data.loop_range_time

        for idx, sequence_item in enumerate(self.data.sequence):

            # do this action based on execution_probability given in the single sequence_item
            if do_action(sequence_item.execution_probability):
                typing_t = int(len(sequence_item.message) / 10) + 1

                if idx == 0:
                    t = randrange(range_min, range_max) - typing_t
                else:
                    t = randrange(2, 4) - typing_t

                total_time += t
                time_actions.append(_ActionDescriptor(t, typing_t, sequence_item.message))

        """
        If the actions have not reached the loop time in total_time add the remaining time
        for match the loop time given. If the actions exceed the loop time this value will be 0.
        """
        remaining_time = self.data.loop_time - total_time
        if remaining_time > 0:
            time_actions.append(_ActionDescriptor(remaining_time, 0, ''))

        return time_actions

    async def start_loop(self, channel):

        self.__stop_action = False
        # on the first call the loop will kick in fast
        first_loop = True

        while True:
            if self.__stop_action:
                break

            if not self.__pause_action:

                time_actions = self._generate_time_action(is_first=first_loop)

                await self.logger.log_action(f'calculated the loop for this task: "{time_actions}"')

                for descriptor in time_actions:

                    await sleep(descriptor.total_time)  # time in seconds to send the command

                    if descriptor.message and not self.__pause_action:
                        await channel.trigger_typing()
                        await sleep(descriptor.typing_time)
                        await channel.send(descriptor.message)

                        await self.logger.log_action(f'sent message: "{descriptor.message}" in location: "{channel}"')

                first_loop = False

            else:
                first_loop = True
                await sleep(1)

    def stop_loop(self):
        self.__stop_action = True

    def pause_loop(self, force: bool):
        if not self.data.run_always or force:
            self.__pause_action = True

    def resume_loop(self, force: bool):
        if not self.data.run_always or force:
            self.__pause_action = False


class ActionsManager:

    def __init__(self, actions_raw):
        self.actions_raw = actions_raw
        self.actions: List[_ActionExecutor] = []

        self.scheduler = Scheduler()

    async def create(self, channel, data: dict, logger):
        action_data = _ActionData(data)
        action = _ActionExecutor(action_data, logger)
        self.actions.append(action)
        await self.scheduler.start_loop(
            await action.start_loop(channel)
        )

    def pause(self, force=False):
        for action in self.actions:
            action.pause_loop(force)

    def resume(self, force=False):
        for action in self.actions:
            action.resume_loop(force)
