import contextlib
import ctypes
import sys
from ctypes import wintypes, Structure
from enum import Enum
from functools import partial
from typing import Optional

from comtypes import GUID as _GUID
from comtypes.automation import VT_BOOL, VT_LPWSTR, VT_EMPTY
from comtypes.persist import STGM_READWRITE
from pycaw.api.mmdeviceapi import PROPERTYKEY
from pycaw.api.mmdeviceapi.depend import PROPVARIANT
from pycaw.utils import AudioUtilities

from util import Singleton

# Load DLL for functions
combase = ctypes.windll.combase
ole32 = ctypes.windll.ole32


# Define Enums
class ERole(Enum):
    eConsole = 0
    eMultimedia = 1
    eCommunications = 2


class EDataFlow(Enum):
    eRender = 0
    eCapture = 1


# Define structures
class GUID(Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]

    @classmethod
    def from_string(cls, guid_string):
        """Convert a GUID string to a GUID structure."""
        parts = guid_string.strip("{}").split("-")
        data4_list = [int(parts[3][i : i + 2], 16) for i in range(0, 4, 2)] + [
            int(parts[4][i : i + 2], 16) for i in range(0, 12, 2)
        ]
        data4 = (wintypes.BYTE * 8)(*data4_list)
        return cls(int(parts[0], 16), int(parts[1], 16), int(parts[2], 16), data4)


SetPersistedFunc = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_void_p,
    ctypes.c_uint,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_void_p,
)
GetPersistedFunc = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_void_p,
    ctypes.c_uint,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_void_p),
)
ClearAllFunc = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)


# Define constants
AUDIO_CLASS_NAME = "Windows.Media.Internal.AudioPolicyConfig"
WIN_VERSION = ".".join(str(s) for s in sys.getwindowsversion()[:3])
AUDIO_POLICY_CONFIG = (
    "{ab3d4648-e242-459f-b02f-541c70306324}"
    if WIN_VERSION >= "10.0.21390"
    else "{2a59116d-6c4f-45e0-a74f-707e3fef9258}"
)
AUDIO_POLICY_CONFIG_GUID = GUID.from_string(AUDIO_POLICY_CONFIG)
LISTEN_SETTING_GUID = "{24DBB0FC-9311-4B3D-9CF0-18FF155639D4}"
CHECKBOX_PID = 1
LISTENING_DEVICE_PID = 0


def get_stripped_id(long_id: str) -> str:
    return long_id and long_id[17:-39]


def set_listening_checkbox(property_store, value: bool):
    checkbox_pk = PROPERTYKEY()
    checkbox_pk.fmtid = _GUID(LISTEN_SETTING_GUID)
    checkbox_pk.pid = CHECKBOX_PID

    new_value = PROPVARIANT(VT_BOOL)
    new_value.union.boolVal = value
    property_store.SetValue(checkbox_pk, new_value)


def set_listening_device(property_store, output_device_id):
    device_pk = PROPERTYKEY()
    device_pk.fmtid = _GUID(LISTEN_SETTING_GUID)
    device_pk.pid = LISTENING_DEVICE_PID

    if output_device_id is not None:
        new_value = PROPVARIANT(VT_LPWSTR)
        new_value.union.pwszVal = output_device_id
    else:
        new_value = PROPVARIANT(VT_EMPTY)

    property_store.SetValue(device_pk, new_value)


def get_device_store(device_id: str):
    enumerator = AudioUtilities.GetDeviceEnumerator()
    dev = enumerator.GetDevice(device_id)

    try:
        store = dev.OpenPropertyStore(STGM_READWRITE)
    except:
        raise RuntimeError("Cannot write to device store, please run as administrator")
    return store


class AudioSessionHandler(metaclass=Singleton):
    def __init__(self):
        ole32.CoInitializeEx(None, 0)
        hstring = wintypes.HSTR()
        if (
            combase.WindowsCreateString(
                AUDIO_CLASS_NAME, len(AUDIO_CLASS_NAME), ctypes.byref(hstring)
            )
            != 0
        ):
            raise Exception("Could not create class name HSTRING")

        factory = ctypes.c_void_p()
        if (
            combase.RoGetActivationFactory(
                hstring, ctypes.byref(AUDIO_POLICY_CONFIG_GUID), ctypes.byref(factory)
            )
            != 0
        ):
            raise Exception("Could not instantiate factory")

        combase.WindowsDeleteString(hstring)
        # TODO: Investigate viability of ActivateInstance over pointers
        vtable_ptr = ctypes.cast(factory, ctypes.POINTER(ctypes.c_void_p))[0]
        vtable = (ctypes.c_void_p * 30).from_address(vtable_ptr)

        _set_persisted_default_audio_endpoint = SetPersistedFunc(vtable[25])
        self._set_persisted_default_audio_endpoint = partial(
            _set_persisted_default_audio_endpoint, factory
        )

        _get_persisted_default_audio_endpoint = GetPersistedFunc(vtable[26])
        self._get_persisted_default_audio_endpoint = partial(
            _get_persisted_default_audio_endpoint, factory
        )

        _clear_all_persisted_application_default_endpoints = ClearAllFunc(vtable[27])
        self._clear_all_persisted_application_default_endpoints = partial(
            _clear_all_persisted_application_default_endpoints, factory
        )

    def get_device_for_process(self, process_id: int, flow: EDataFlow, role: ERole):
        _device_id = wintypes.HSTR()
        hr = self._get_persisted_default_audio_endpoint(
            process_id, flow.value, role.value, ctypes.byref(_device_id)
        )
        if hr != 0:
            raise Exception("Could not get device id")

        length = ctypes.c_uint32()
        # TODO: Switch to using the buffer at some point
        buffer = combase.WindowsGetStringRawBuffer(_device_id, ctypes.byref(length))
        if length.value == 0:
            # It's the default setting, so no ID is given
            return None
        # Using the outputted buffer results in read violations, yet this consistently works somehow???
        return ctypes.wstring_at(_device_id.value + 28, length.value)

    def set_device_for_process(self, process_id: int, flow: EDataFlow, device_id: str):
        if device_id:
            device_id_hstr = wintypes.HSTR()
            if (
                combase.WindowsCreateString(
                    device_id, len(device_id), ctypes.byref(device_id_hstr)
                )
                != 0
            ):
                raise Exception("Could not create device ID HSTRING")
        else:
            # Reset to default
            device_id_hstr = None

        for role in range(
            3
        ):  # Set for ERole.eConsole, ERole.eMultimedia, and ERole.eCommunications
            hr_role = self._set_persisted_default_audio_endpoint(
                process_id, flow.value, role, device_id_hstr
            )
            if hr_role != 0:
                raise Exception(f"Could not set device for {ERole(role).name}")

        if device_id:
            combase.WindowsDeleteString(device_id_hstr)

    def __del__(self):
        ole32.CoUninitialize()


@contextlib.contextmanager
def switch_output_device_for_process(process_id: int, new_device_id: str):
    mgr = AudioSessionHandler()
    current_device_id = mgr.get_device_for_process(
        process_id, EDataFlow.eRender, ERole.eMultimedia
    )
    mgr.set_device_for_process(process_id, EDataFlow.eRender, new_device_id)
    print("Switched output device")
    yield
    mgr.set_device_for_process(process_id, EDataFlow.eRender, current_device_id)
    print("Switched back")


@contextlib.contextmanager
def listen_to_input_device(input_device: str, output_device: Optional[str]):
    stripped_input_id = get_stripped_id(input_device)
    print(stripped_input_id)
    stripped_output_id = get_stripped_id(output_device)
    store = get_device_store(stripped_input_id)
    if not store:
        raise RuntimeError("Device listening setting unavailable")

    set_listening_checkbox(store, True)
    set_listening_device(store, stripped_output_id)
    print("Listening to output device")
    yield
    set_listening_checkbox(store, False)
    set_listening_device(store, None)
    print("No longer listening to output device")
