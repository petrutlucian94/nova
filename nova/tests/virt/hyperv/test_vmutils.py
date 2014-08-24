# Copyright 2013 Cloudbase Solutions Srl
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

import mock

from nova import test

from nova.virt.hyperv import constants
from nova.virt.hyperv import hostutils
from nova.virt.hyperv import vmutils


class VMUtilsTestCase(test.NoDBTestCase):
    """Unit tests for the Hyper-V VMUtils class."""

    _FAKE_VM_NAME = 'fake_vm'
    _FAKE_MEMORY_MB = 2
    _FAKE_VM_PATH = "fake_vm_path"
    _FAKE_VHD_PATH = "fake_vhd_path"
    _FAKE_DVD_PATH = "fake_dvd_path"
    _FAKE_VOLUME_DRIVE_PATH = "fake_volume_drive_path"
    _FAKE_CONTROLLER_PATH = "fake_controller_path"
    _FAKE_ADDRESS = "fake_address"
    _FAKE_MOUNTED_DISK_PATH = "fake_mounted_disk_path"

    @mock.patch.object(hostutils.HostUtils, "check_min_windows_version")
    def setUp(self, mock_check_min_windows_version):
        self._vmutils = vmutils.VMUtils()
        self._vmutils._conn = mock.MagicMock()

        super(VMUtilsTestCase, self).setUp()

    def test_enable_vm_metrics_collection(self):
        self.assertRaises(NotImplementedError,
                          self._vmutils.enable_vm_metrics_collection,
                          self._FAKE_VM_NAME)

    def _lookup_vm(self):
        mock_vm = mock.MagicMock()
        self._vmutils._lookup_vm_check = mock.MagicMock(
            return_value=mock_vm)
        mock_vm.path_.return_value = self._FAKE_VM_PATH
        return mock_vm

    def test_set_vm_memory_static(self):
        self._test_set_vm_memory_dynamic(1.0)

    def test_set_vm_memory_dynamic(self):
        self._test_set_vm_memory_dynamic(2.0)

    def _test_set_vm_memory_dynamic(self, dynamic_memory_ratio):
        mock_vm = self._lookup_vm()

        mock_s = self._vmutils._conn.Msvm_VirtualSystemSettingData()[0]
        mock_s.SystemType = 3

        mock_vmsetting = mock.MagicMock()
        mock_vmsetting.associators.return_value = [mock_s]

        self._vmutils._modify_virt_resource = mock.MagicMock()

        self._vmutils._set_vm_memory(mock_vm, mock_vmsetting,
                                     self._FAKE_MEMORY_MB,
                                     dynamic_memory_ratio)

        self._vmutils._modify_virt_resource.assert_called_with(
            mock_s, self._FAKE_VM_PATH)

        if dynamic_memory_ratio > 1:
            self.assertTrue(mock_s.DynamicMemoryEnabled)
        else:
            self.assertFalse(mock_s.DynamicMemoryEnabled)

    @mock.patch('nova.virt.hyperv.vmutils.VMUtils._get_vm_disks')
    def test_get_vm_storage_paths(self, mock_get_vm_disks):
        self._lookup_vm()
        mock_rasds = self._create_mock_disks()
        mock_get_vm_disks.return_value = ([mock_rasds[0]], [mock_rasds[1]])

        storage = self._vmutils.get_vm_storage_paths(self._FAKE_VM_NAME)
        (disk_files, volume_drives) = storage

        self.assertEqual([self._FAKE_VHD_PATH], disk_files)
        self.assertEqual([self._FAKE_VOLUME_DRIVE_PATH], volume_drives)

    def test_get_vm_disks(self):
        mock_vm = self._lookup_vm()
        mock_vmsettings = [mock.MagicMock()]
        mock_vm.associators.return_value = mock_vmsettings

        mock_rasds = self._create_mock_disks()
        mock_vmsettings[0].associators.return_value = mock_rasds

        (disks, volumes) = self._vmutils._get_vm_disks(mock_vm)

        mock_vm.associators.assert_called_with(
            wmi_result_class=self._vmutils._VIRTUAL_SYSTEM_SETTING_DATA_CLASS)
        mock_vmsettings[0].associators.assert_called_with(
            wmi_result_class=self._vmutils._STORAGE_ALLOC_SETTING_DATA_CLASS)
        self.assertEqual([mock_rasds[0]], disks)
        self.assertEqual([mock_rasds[1]], volumes)

    def _create_mock_disks(self):
        mock_rasd1 = mock.MagicMock()
        mock_rasd1.ResourceSubType = self._vmutils._IDE_DISK_RES_SUB_TYPE
        mock_rasd1.Connection = [self._FAKE_VHD_PATH]
        mock_rasd1.Parent = self._FAKE_CONTROLLER_PATH
        mock_rasd1.Address = self._FAKE_ADDRESS
        mock_rasd1.HostResource = [self._FAKE_MOUNTED_DISK_PATH]

        mock_rasd2 = mock.MagicMock()
        mock_rasd2.ResourceSubType = self._vmutils._PHYS_DISK_RES_SUB_TYPE
        mock_rasd2.HostResource = [self._FAKE_VOLUME_DRIVE_PATH]

        return [mock_rasd1, mock_rasd2]

    def test_list_instance_notes(self):
        vs = mock.MagicMock()
        attrs = {'ElementName': 'fake_name',
                 'Notes': '4f54fb69-d3a2-45b7-bb9b-b6e6b3d893b3'}
        vs.configure_mock(**attrs)
        self._vmutils._conn.Msvm_VirtualSystemSettingData.return_value = [vs]
        response = self._vmutils.list_instance_notes()

        self.assertEqual(response, [(attrs['ElementName'], [attrs['Notes']])])
        self._vmutils._conn.Msvm_VirtualSystemSettingData.assert_called_with(
            ['ElementName', 'Notes'],
            SettingType=self._vmutils._VIRTUAL_SYSTEM_CURRENT_SETTINGS)

    def test_list_instances(self):
        vs = mock.MagicMock()
        attrs = {'ElementName': 'fake_name'}
        vs.configure_mock(**attrs)
        self._vmutils._conn.Msvm_VirtualSystemSettingData.return_value = [vs]
        response = self._vmutils.list_instances()

        self.assertEqual(response, [(attrs['ElementName'])])
        self._vmutils._conn.Msvm_VirtualSystemSettingData.assert_called_with(
            ['ElementName'],
            SettingType=self._vmutils._VIRTUAL_SYSTEM_CURRENT_SETTINGS)

    def test_set_disk_host_resource(self):
        fake_new_mounted_disk_path = 'fake_new_mounted_disk_path'

        self._lookup_vm()
        mock_rasds = self._create_mock_disks()

        self._vmutils._get_vm_disks = mock.MagicMock(
            return_value=([mock_rasds[0]], [mock_rasds[1]]))
        self._vmutils._modify_virt_resource = mock.MagicMock()
        self._vmutils._get_disk_resource_address = mock.MagicMock(
            return_value=self._FAKE_ADDRESS)

        self._vmutils.set_disk_host_resource(
            self._FAKE_VM_NAME,
            self._FAKE_CONTROLLER_PATH,
            self._FAKE_ADDRESS,
            fake_new_mounted_disk_path)
        self._vmutils._get_disk_resource_address.assert_called_with(
            mock_rasds[0])
        self._vmutils._modify_virt_resource.assert_called_with(
            mock_rasds[0], self._FAKE_VM_PATH)
        self.assertEqual(
            mock_rasds[0].HostResource[0], fake_new_mounted_disk_path)

    @mock.patch.object(vmutils.VMUtils, "_clone_wmi_obj")
    def _test_check_clone_wmi_obj(self, mock_clone_wmi_obj, clone_objects):
        mock_obj = mock.MagicMock()
        self._vmutils._clone_wmi_objs = clone_objects

        response = self._vmutils._check_clone_wmi_obj(class_name="fakeClass",
                                                      obj=mock_obj)
        if not clone_objects:
            self.assertEqual(mock_obj, response)
        else:
            mock_clone_wmi_obj.assert_called_once_with("fakeClass", mock_obj)
            self.assertEqual(mock_clone_wmi_obj.return_value, response)

    def test_check_clone_wmi_obj_true(self):
        self._test_check_clone_wmi_obj(clone_objects=True)

    def test_check_clone_wmi_obj_false(self):
        self._test_check_clone_wmi_obj(clone_objects=False)

    def test_clone_wmi_obj(self):
        mock_obj = mock.MagicMock()
        mock_value = mock.MagicMock()
        mock_value.Value = mock.sentinel.fake_value
        mock_obj._properties = [mock.sentinel.property]
        mock_obj.Properties_.Item.return_value = mock_value

        response = self._vmutils._clone_wmi_obj(
            class_name="FakeClass", obj=mock_obj)

        compare = self._vmutils._conn.FakeClass.new()
        self.assertEqual(mock.sentinel.fake_value,
                         compare.Properties_.Item().Value)
        self.assertEqual(compare, response)
