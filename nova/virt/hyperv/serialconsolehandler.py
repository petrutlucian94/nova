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

from eventlet import patcher
from oslo.config import cfg

from nova.console import type as ctype
from nova.console import serial as serial_console
from nova import exception
from nova.i18n import _
from nova.openstack.common import log as logging
from nova.virt.hyperv import ioutils
from nova.virt.hyperv import namedpipe
from nova.virt.hyperv import serialproxy
from nova.virt.hyperv import utilsfactory
from nova.virt.hyperv import vmutils

CONF = cfg.CONF

threading = patcher.original('threading')


class SerialConsoleHandler(object):
    """Handles serial console ops for related to a given instance."""
    def __init__(self, instance_name):
        self._vmutils = utilsfactory.get_vmutils()
        self._pathutils = utilsfactory.get_pathutils()

        self._instance_name = instance_name
        pipe_name = self._vmutils.get_vm_serial_port_connection(
            instance_name)

        if not pipe_name:
            err_msg = _(
                "Serial port is not available for vm %s") % instance_name
            raise vmutils.HyperVException(err_msg)

        self.console_log_paths = self._pathutils.get_vm_console_log_paths(
            instance_name)

        self._client_connected = threading.Event()
        self._running_event = threading.Event()

        self._input_queue = ioutils.IOQueue(
            client_connected=self._client_connected)
        self._output_queue = ioutils.IOQueue(
            self._client_connected)

        self._setup_tcp_listener()
        self._named_pipe_handler = namedpipe.NamedPipeHandler(
            pipe_name, self._input_queue, self._output_queue,
            self._client_connected, self.console_log_paths[0])

    def start(self):
        if self._listener:
            self._listener.start()
        self._named_pipe_handler.start()

    def _setup_tcp_listener(self):
        if CONF.serial_console.enabled:
            self._listen_host = (
                CONF.serial_console.proxyclient_address)
            self._listen_port = serial_console.acquire_port(
                self._listen_host)

            self._listener = serialproxy.SerialProxyListener(
                self._instance_name, self._listen_host,
                self._listen_port, self._input_queue,
                self._output_queue, self._client_connected)
        else:
            self._listener = None

    def stop(self, delete_logs=False):
        if self._listener:
            self._listener.stop()
            serial_console.release_port(self._listen_host, self._listen_port)

        self._named_pipe_handler.stop()

        if delete_logs:
            self._pathutils.delete_vm_console_logs(self._instance_name)
 
    def get_serial_console(self):
        if not CONF.serial_console.enabled:
            raise exception.ConsoleTypeUnavailable(console_type='serial')
        return ctype.ConsoleSerial(host=self._listen_host,
                                   port=self._listen_port)

    def get_console_output(self):
        try:
            log = ''
            # Start with the oldest console log file.
            for log_path in self.console_log_paths[::-1]:
                if os.path.exists(log_path):
                    with open(log_path, 'rb') as fp:
                        log += fp.read()
            return log
        except IOError as err:
            msg = (_("Could not get instance %(instance_name)s "
                     "console output. Error: %(err)s") %
                   {'instance_name': self._instance_name,
                    'err': err})
            raise vmutils.HyperVException(msg)
