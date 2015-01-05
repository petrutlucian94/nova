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

from eventlet import patcher
from oslo.utils import excutils


from nova.i18n import _LE
from nova.openstack.common import log as logging
from nova.virt.hyperv import constants
from nova.virt.hyperv import ioutils

threading = patcher.original('threading')
time = patcher.original('time')

LOG = logging.getLogger(__name__)


WAIT_PIPE_TIMEOUT = 5 # seconds


class NamedPipeHandler(object):
    """Handles asyncronous I/O operations on a specified named pipe."""

    def __init__(self, pipe_name, input_queue, output_queue,
                 connect_event, log_file):
        self._pipe_name = pipe_name
        self._input_queue = input_queue
        self._output_queue = output_queue
        self._log_file = log_file

        self._client_connected = connect_event
        self._stopped = threading.Event()
        self._workers = []

        self._ioutils = ioutils.IOUtils()

        self._setup_io_structures()

    def start(self):
        self._stopped.clear()
        self._open_pipe()
        self._log_file_handle = open(self._log_file, 'ab', 1)

        for job in [self._read_from_pipe, self._write_to_pipe]:
            worker = threading.Thread(target=job)
            worker.setDaemon(True)
            worker.start()
            self._workers.append(worker)

    def stop(self):
        if not self._stopped.isSet():
            self._stopped.set()
            self._close_pipe()
            # Signal IO completion
            self._ioutils.set_event(self._r_overlapped.hEvent)
            self._ioutils.set_event(self._w_overlapped.hEvent)
            self._log_file_handle.close()
            for worker in self._workers:
                worker.join()

    def _setup_io_structures(self):
        self._r_buffer = self._ioutils.get_buffer(
            constants.SERIAL_CONSOLE_BUFFER_SIZE)
        self._w_buffer = self._ioutils.get_buffer(
            constants.SERIAL_CONSOLE_BUFFER_SIZE)

        self._r_overlapped = self._ioutils.get_new_overlapped_structure()
        self._w_overlapped = self._ioutils.get_new_overlapped_structure()

        self._r_completion_routine = self._ioutils.get_completion_routine(
            self._read_callback)
        self._w_completion_routine = self._ioutils.get_completion_routine()

    def _open_pipe(self):
        """Opens a named pipe in overlapped mode for asyncronous I/O."""
        self._ioutils.wait_named_pipe(self._pipe_name, WAIT_PIPE_TIMEOUT)

        self._pipe_handle = self._ioutils.open(
            self._pipe_name,
            desired_access=(ioutils.GENERIC_READ | ioutils.GENERIC_WRITE),
            share_mode=(ioutils.FILE_SHARE_READ | ioutils.FILE_SHARE_WRITE),
            creation_disposition=ioutils.OPEN_EXISTING,
            flags_and_attributes=ioutils.FILE_FLAG_OVERLAPPED)

    def _close_pipe(self):
        if not self._pipe_handle:
            return

        self._ioutils.cancel_io(self._pipe_handle)
        self._ioutils.close_handle(self._pipe_handle)
        self._pipe_handle = None

    def _read_from_pipe(self):
        self._start_io_worker(self._ioutils.read,
                              self._r_buffer,
                              self._r_overlapped,
                              self._r_completion_routine)

    def _write_to_pipe(self):
        self._start_io_worker(self._ioutils.write,
                              self._w_buffer,
                              self._w_overlapped,
                              self._w_completion_routine,
                              self._get_data_to_write)

    def _start_io_worker(self, func, buff, overlapped_structure,
                         completion_routine, buff_update_func=None):
        try:
            while not self._stopped.isSet():
                if buff_update_func:
                    num_bytes = buff_update_func()
                    if not num_bytes:
                        continue
                else:
                    num_bytes = len(buff)

                func(self._pipe_handle, buff, num_bytes,
                     overlapped_structure, completion_routine)
        except Exception as err:
            LOG.error(_LE("Named pipe handler exception. "
                          "Pipe Name: %(pipe_name)s "
                          "Error: %(err)s"),
                      {'pipe_name': self._pipe_name,
                       'err': err})
            self.stop()

    def _read_callback(self, num_bytes):
        data = self._ioutils.get_buffer_data(self._r_buffer,
                                             num_bytes)
        self._output_queue.put(data)

        self._write_to_log(data)

    def _get_data_to_write(self):
        while not (self._stopped.isSet() or self._client_connected.isSet()):
            time.sleep(1)

        data = self._input_queue.get()
        if data:
            self._ioutils.write_buffer_data(self._w_buffer, data)
            return len(data)
        return 0

    def _write_to_log(self, data):
        if self._stopped.isSet():
            return

        try:
            log_size = self._log_file_handle.tell() + len(data)
            if (log_size >= constants.MAX_CONSOLE_LOG_FILE_SIZE):
                self._log_file.flush()
                self._log_file_handle.close()
                log_archive_path = self._log_file + '.1'
                if os.path.exists(log_archive_path):
                    os.remove(log_archive_path)
                    os.rename(self._log_file_path, log_archive_path)
                    self._log_file_handle = open(
                        self._log_file_path, 'ab', 1)
            self._log_file_handle.write(data)
        except IOError as err:
            LOG.error(_LE("Named pipe handler exception. "
                          "Pipe Name: %(pipe_name)s "
                          "Error: %(err)s"),
                      {'pipe_name': self._pipe_name,
                       'err': err})
