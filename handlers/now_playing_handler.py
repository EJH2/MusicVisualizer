import datetime
from io import BytesIO
from typing import Optional

import psutil
from pycaw.utils import AudioUtilities
from winrt.windows.devices.enumeration import DeviceInformation, DeviceClass
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
    GlobalSystemMediaTransportControlsSession as Session,
)
from winrt.windows.storage.streams import (
    Buffer,
    InputStreamOptions,
    IRandomAccessStreamReference,
)

_MUSIC_PROCESS_ID = None
CALLBACK_SET = False


async def get_device_id_from_name(name: str) -> Optional[str]:
    devices = await DeviceInformation.find_all_async_device_class(
        DeviceClass.AUDIO_RENDER
    )
    device = next((d for d in devices if name in d.name), None)
    if not device:
        devices = await DeviceInformation.find_all_async_device_class(
            DeviceClass.AUDIO_CAPTURE
        )
        device = next((d for d in devices if name in d.name), None)
    return None if not device else device.id


async def get_device_name_from_id(device_id: str) -> Optional[str]:
    devices = await DeviceInformation.find_all_async_device_class(
        DeviceClass.AUDIO_RENDER
    )
    device = next((d for d in devices if d.id == device_id), None)
    if not device:
        devices = await DeviceInformation.find_all_async_device_class(
            DeviceClass.AUDIO_CAPTURE
        )
        device = next((d for d in devices if d.id == device_id), None)
    return None if not device else device.name


def get_music_process_pid(name: str) -> Optional[int]:
    global _MUSIC_PROCESS_ID
    if _MUSIC_PROCESS_ID:
        try:
            if name in psutil.Process(_MUSIC_PROCESS_ID).name().lower():
                return _MUSIC_PROCESS_ID
        except psutil.NoSuchProcess:
            pass

    sessions = AudioUtilities.GetAllSessions()
    music_process = next((s for s in sessions if name.lower() in str(s).lower()), None)
    _MUSIC_PROCESS_ID = None if not music_process else music_process.ProcessId
    return _MUSIC_PROCESS_ID


async def get_music_session(name: str) -> Session:
    session_manager = await SessionManager.request_async()
    sessions = session_manager.get_sessions()
    music_session = next(
        (s for s in sessions if name.lower() in s.source_app_user_model_id.lower()),
        None,
    )

    if music_session is None:
        raise Exception("No music session found")

    return music_session


async def get_watermarked_thumbnail(thumbnail: IRandomAccessStreamReference) -> BytesIO:
    readable_stream = await thumbnail.open_read_async()
    # noinspection PyPropertyAccess
    thumb_read_buffer = Buffer(readable_stream.size)
    await readable_stream.read_async(
        thumb_read_buffer, thumb_read_buffer.capacity, InputStreamOptions.READ_AHEAD
    )

    binary = BytesIO()
    binary.write(bytearray(thumb_read_buffer))
    binary.seek(0)

    return binary


def get_human_timestamp_from_timedelta(timedelta: datetime.timedelta) -> str:
    total_seconds = int(timedelta.total_seconds())
    seconds = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = (total_seconds // 3600) % 24
    return f"{f'{hours}:{minutes:02}' if hours else f'{minutes}'}:{seconds:02}"


def get_media_timeline_data(
    session: Session,
) -> tuple[datetime.timedelta, datetime.timedelta]:
    timeline_data = session.get_timeline_properties()
    current_time = timeline_data.position
    total_time = timeline_data.end_time

    # noinspection PyTypeChecker
    return current_time, total_time


def get_media_playback_status(session: Session):
    playback_info = session.get_playback_info()
    return playback_info.playback_status


async def get_media_session_data(session: Session) -> tuple[str, list[str]]:
    song_properties = await session.try_get_media_properties_async()
    title = song_properties.title
    artist = song_properties.artist

    # noinspection PyTypeChecker
    return title, [artist]
