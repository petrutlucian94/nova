# Copyright 2015 Cloudbase Solutions Srl
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import eventlet

import sys

if sys.platform == 'win32':
    import wmi

from oslo_config import cfg
from oslo_log import log as logging

from nova import exception
from nova.i18n import _LW
from nova.virt import event as virtevent
from nova.virt.hyperv import constants
from nova.virt.hyperv import utilsfactory

LOG = logging.getLogger(__name__)

hyperv_opts = [
    cfg.IntOpt('power_state_check_timeframe',
                default=60,
                help='The timeframe to be checked for instance power '
                     'state changes.'),
    cfg.IntOpt('power_state_event_polling_interval',
                default=2,
                help='Instance power state change event polling frequency.'),

]

CONF = cfg.CONF
CONF.register_opts(hyperv_opts, 'hyperv')


class InstanceEventHandler(object):
    _MODIFICATION_EVENT = 'modification'
    # The event listener timeout is set to 0 in order to return immediately
    # and avoid blocking the thread.
    _WAIT_TIMEOUT = 0

    _TRANSITION_MAP = {
        constants.HYPERV_VM_STATE_ENABLED: virtevent.EVENT_LIFECYCLE_STARTED,
        constants.HYPERV_VM_STATE_DISABLED: virtevent.EVENT_LIFECYCLE_STOPPED,
        constants.HYPERV_VM_STATE_PAUSED: virtevent.EVENT_LIFECYCLE_PAUSED,
        constants.HYPERV_VM_STATE_SUSPENDED:
            virtevent.EVENT_LIFECYCLE_SUSPENDED
    }

    def __init__(self, state_change_callback=None,
                 running_state_callback=None):
        self._vmutils = utilsfactory.get_vmutils()
        self._listener = self._vmutils.get_vm_state_change_listener(
            event_type=self._MODIFICATION_EVENT,
            timeframe=CONF.hyperv.power_state_check_timeframe)

        self._polling_interval = CONF.hyperv.power_state_event_polling_interval
        self._state_change_callback = state_change_callback
        self._running_state_callback = running_state_callback

        self._last_state = {}

    def start_listener(self):
        eventlet.spawn_n(self._poll_events)

    def _poll_events(self):
        while True:
            try:
                # Retrieve one by one all the events that occured in
                # the checked interval.
                event = self._listener(self._WAIT_TIMEOUT)
                self._dispatch_event(event)
                continue
            except wmi.x_wmi_timed_out:
                # If no events were triggered in the checked interval,
                # a timeout exception is raised. We'll just ignore it.
                pass

            eventlet.sleep(self._polling_interval)

    def _dispatch_event(self, event):
        # Hyper-V generated GUID
        instance_guid = event.Name
        instance_state = self._vmutils.get_vm_power_state(event.EnabledState)
        instance_name = event.ElementName

        # The event listener returns all instance related changes. We
        # ignore events that are not triggered by power state changes
        # as well as intermediary states.
        last_state = self._last_state.get(instance_guid)

        state_unchanged = last_state == instance_state
        is_intermediary_state = instance_state not in self._TRANSITION_MAP
        self._last_state[instance_guid] = instance_state

        if state_unchanged or is_intermediary_state:
            return

        # Instance uuid set by Nova. If this is missing, we assume that
        # the instance was not created by Nova and ignore the event.
        instance_uuid = self._get_instance_uuid(instance_name)
        if instance_uuid:
            self._emit_event(instance_name, instance_uuid, instance_state)

    def _emit_event(self, instance_name, instance_uuid, instance_state):
        virt_event = self._get_virt_event(instance_uuid,
                                          instance_state)
        eventlet.spawn_n(self._state_change_callback, virt_event)

        if instance_state == constants.HYPERV_VM_STATE_ENABLED:
            eventlet.spawn_n(self._running_state_callback,
                             instance_name, instance_uuid)

    def _get_instance_uuid(self, instance_name):
        try:
            instance_uuid = self._vmutils.get_instance_uuid(instance_name)
            if not instance_uuid:
                LOG.warn(_LW("Instance uuid could not be retrieved for "
                             "instance %s. Instance state change event "
                             "will be ignored."),
                         instance_name)
            return instance_uuid
        except exception.NotFound:
            # The instance has been deleted.
            pass

    def _get_virt_event(self, instance_uuid, instance_state):
        transition = self._TRANSITION_MAP[instance_state]
        return virtevent.LifecycleEvent(uuid=instance_uuid,
                                        transition=transition)
