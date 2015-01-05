# Copyright 2014 Cloudbase Solutions Srl
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

import os

from oslo.config import cfg

from nova.i18n import _LW
from nova import utils
from nova.openstack.common import log as logging
from nova.virt.hyperv import serialconsolehandler
from nova.virt.hyperv import utilsfactory
from nova.virt.hyperv import vmutils

CONF = cfg.CONF

LOG = logging.getLogger(__name__)

def instance_synchronized(func):
    def wrapper(self, instance_name, *args, **kwargs):
        @utils.synchronized(instance_name)
        def inner():
            return func(self, instance_name, *args, **kwargs)
        return inner()
    return wrapper      

class SerialConsoleOps(object):
    def __init__(self):
        self._vmutils = utilsfactory.get_vmutils()
        self._pathutils = utilsfactory.get_pathutils()

        self._console_handlers = {}

    @instance_synchronized
    def start_console_handler(self, instance_name):
        if not self._console_handlers.get(instance_name):
            handler = serialconsolehandler.SerialConsoleHandler(
                instance_name)
            handler.start()
            self._console_handlers[instance_name] = handler

    @instance_synchronized
    def stop_console_handler(self, instance_name, delete_logs=False):
        handler = self._console_handlers.get(instance_name)
        if handler:
            handler.stop(delete_logs)
            del self._console_handlers[instance_name]

    @instance_synchronized
    def get_serial_console(self, instance_name):
        handler = self._console_handlers.get(instance_name)
        if not handler:
            raise exception.ConsoleTypeUnavailable(console_type='serial')
        return handler.get_serial_console()

    @instance_synchronized
    def get_console_output(self, instance_name):
        handler = self._console_handlers.get(instance_name)
        if handler:
            return handler.get_console_output()
        LOG.warn(_LW("Cannot get console output for instance %s. Serial "
                     "console handler is not available"), instance_name)
        return ''

    def start_vm_console_handlers(self):
        active_instances = self._vmutils.get_active_instances()
        for instance_name in active_instances:
            instance_path = self._pathutils.get_instance_dir(instance_name)

            # Skip instances that are not created by Nova
            if not os.path.exists(instance_path):
                continue

            self.start_console_handler(instance_name)
 