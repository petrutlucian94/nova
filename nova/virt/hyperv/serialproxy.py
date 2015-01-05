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
import errno
import socket

from nova.i18n import _, _LE, _LI
from nova.virt.hyperv import constants
from nova.openstack.common import log as logging

threading = patcher.original('threading')

LOG = logging.getLogger(__name__)


def handle_socket_errors(func):
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except socket.error as error:
            # Ignore errors caused by closed sockets
            if not error.errno == errno.EBADF:
                LOG.error(_LE('Serial proxy got an error while handling '
                              'connnection to instance %(instance_name)s. '
                              'Closing connection. Error: %(error)s '),
                          {'instance_name': self._instance_name,
                           'error': error})
            self._client_connected.clear()
    return wrapper 


class SerialProxyListener(threading.Thread):
    def __init__(self, instance_name, addr, port, input_queue,
                 output_queue, connect_event):
        super(SerialProxyListener, self).__init__()
        self.setDaemon(True)

        self._instance_name = instance_name
        self._addr = addr
        self._port = port
        self._conn = None

        LOG.debug('Ininitializing serial proxy on %(addr)s:%(port)s, '
                  'handling connections to instance %(instance_name)s.',
                  {'addr': self._addr, 'port': self._port, 
                   'instance_name': self._instance_name})

        self._sock = socket.socket(socket.AF_INET,
                                   socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET,
                              socket.SO_REUSEADDR,
                              1)
        self._sock.bind((self._addr, self._port))
        self._sock.listen(1)
 
        self._input_queue = input_queue
        self._output_queue = output_queue
        self._client_connected = connect_event
        self._stopped = threading.Event()

    def stop(self):
        LOG.debug("Stopping instance %s serial proxy handler. Clients will be "
                  "disconnected.", self._instance_name)
        self._stopped.set()
        self._client_connected.clear()
        if self._conn:
            self._conn.shutdown(socket.SHUT_RDWR)
            self._conn.close()
        self._sock.close()

    def run(self):
        while not self._stopped.isSet():
            self._accept_conn()

    @handle_socket_errors
    def _accept_conn(self):
        self._conn, client_addr = self._sock.accept()

        LOG.info(_LI('Incomming connection from %(client_addr)s '
                     'on %(addr)s:%(port)s to instance: %(instance_name)s') %
                 {'client_addr': client_addr[0],
                  'addr': self._addr,
                  'port': self._port, 
                  'instance_name': self._instance_name})
        self._client_connected.set()

        workers = []
        for job in [self._get_data, self._send_data]:
            worker = threading.Thread(target=job)
            worker.setDaemon(True)
            worker.start()
            workers.append(worker)

        for worker in workers:
            worker.join()

        LOG.info(_LI('Client %(client_addr)s disconnected from instance '
                     '%(instance_name)s'),
                 {'client_addr': client_addr[0],
                  'instance_name': self._instance_name})
        self._conn.close()
        self._conn = None

    @handle_socket_errors
    def _get_data(self):
        while self._client_connected.isSet():
            data = self._conn.recv(constants.SERIAL_CONSOLE_BUFFER_SIZE)
            if not data:
                self._client_connected.clear()
                return
            self._input_queue.put(data)

    @handle_socket_errors
    def _send_data(self):
        while self._client_connected.isSet():
            data = self._output_queue.get()
            if data:
                self._conn.sendall(data)
