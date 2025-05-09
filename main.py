import asyncio
import datetime
import importlib
import textwrap
from typing import Optional

import numpy as np
import pygame
import sounddevice as sd
from PIL import Image
from numpy.fft import fftfreq, fft

from adapters.base_adapter import BaseAdapter
from handlers.now_playing_handler import (
    Session,
    PlaybackStatus,
    get_music_session,
    get_device_id_from_name,
    get_music_process_pid,
    get_media_session_data,
    get_media_timeline_data,
    get_human_timestamp_from_timedelta,
    get_media_playback_status,
)
from handlers.sound_device_handler import (
    switch_output_device_for_process,
    listen_to_input_device,
)
from util import Config

# Set up config constants
config = Config()
settings = config.get_section("MAIN")
try:
    MUSIC_PROGRAM_NAME = settings["music_program"]
except KeyError:
    raise Exception("Music program must be defined in config.ini")

# Set up device constants
CABLE_MIC = next(
    device for device in sd.query_devices() if settings["cable_mic"] in device["name"]
)
CABLE_SPEAKER = next(
    device
    for device in sd.query_devices()
    if settings["cable_speakers"] in device["name"]
)
INPUT_SAMPLERATE = CABLE_MIC.get("default_samplerate", 44100)
OUTPUT_DEVICE = sd.default.device
CHANNELS = 2

# Set up pygame
pygame.init()
pygame.font.init()
timer = pygame.USEREVENT + 1
pygame.time.set_timer(timer, 1000)
title_font = pygame.font.SysFont("Bauhaus 93", 76)
artist_font = pygame.font.SysFont("Bauhaus 93", 42)
time_font = pygame.font.SysFont("Bauhaus 93", 24)
SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
THUMBNAIL_SIZE = SCREEN_WIDTH // 7
TIMELINE_HEIGHT = SCREEN_HEIGHT // 180
screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
visualizer_surface = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
visualizer_surface_rect = visualizer_surface.get_rect()
timeline_surface = pygame.Surface(
    (SCREEN_WIDTH - THUMBNAIL_SIZE, TIMELINE_HEIGHT), pygame.SRCALPHA
)
timeline_surface_rect = timeline_surface.get_rect().move(
    THUMBNAIL_SIZE * 0.5,
    SCREEN_HEIGHT - (THUMBNAIL_SIZE * 0.42) - TIMELINE_HEIGHT,
)
background_entity = pygame.image.load(settings["background_path"])
background_entity = pygame.transform.scale(
    background_entity, (SCREEN_WIDTH, SCREEN_HEIGHT)
)
background_rect = background_entity.get_rect()
thumbnail_entity = pygame.image.load(settings["backup_thumb_path"])
thumbnail_entity = pygame.transform.scale(
    thumbnail_entity, (THUMBNAIL_SIZE, THUMBNAIL_SIZE)
)
thumbnail_rect = thumbnail_entity.get_rect().move(
    THUMBNAIL_SIZE * 0.5, SCREEN_HEIGHT - (THUMBNAIL_SIZE * 1.5)
)
title_entity = title_font.render("Adjudicate", True, (255, 255, 255))
title_rect = title_entity.get_rect().move(
    THUMBNAIL_SIZE * 1.55,
    SCREEN_HEIGHT - (THUMBNAIL_SIZE * 0.65) - title_entity.get_height(),
)
artist_entity = artist_font.render("Borealising", True, (175, 175, 175))
artist_rect = artist_entity.get_rect().move(
    THUMBNAIL_SIZE * 1.55,
    SCREEN_HEIGHT - (THUMBNAIL_SIZE * 0.5) - artist_entity.get_height(),
)
CURRENT_SONG_TIME = datetime.timedelta(0)
current_time_entity = time_font.render(
    get_human_timestamp_from_timedelta(CURRENT_SONG_TIME), True, (255, 255, 255)
)
current_time_rect = current_time_entity.get_rect().move(
    THUMBNAIL_SIZE * 0.5,
    SCREEN_HEIGHT - (THUMBNAIL_SIZE * 0.25) - current_time_entity.get_height(),
)
TOTAL_SONG_TIME = datetime.timedelta(0)
total_time_entity = time_font.render(
    get_human_timestamp_from_timedelta(TOTAL_SONG_TIME), True, (255, 255, 255)
)
total_time_rect = total_time_entity.get_rect().move(
    SCREEN_WIDTH - (THUMBNAIL_SIZE * 0.5) - total_time_entity.get_width(),
    SCREEN_HEIGHT - (THUMBNAIL_SIZE * 0.25) - total_time_entity.get_height(),
)

# Try to set up API Manager
try:
    music_mgr: Optional[BaseAdapter] = importlib.import_module(
        f"adapters.{MUSIC_PROGRAM_NAME}_adapter"
    ).ApiAdapter()
except ImportError:
    print("Could not fetch platform-specific adapter")
    music_mgr = None


def avg_chunk_array(arr: np.array, chunk_size: int) -> np.array:
    num_chunks = (len(arr) + chunk_size - 1) // chunk_size
    chunked_arr = np.array(
        [np.mean(arr[i * chunk_size : (i + 1) * chunk_size]) for i in range(num_chunks)]
    )
    return chunked_arr


def generate_waveform_points(indata: np.array, frames: int) -> tuple[list, list, list]:
    left_channel = indata[:, 0]
    right_channel = indata[:, 1]

    # TODO: Isolate like middle third of each range for display
    xf = fftfreq(frames, 1 / INPUT_SAMPLERATE)
    xf = xf - np.min(xf)
    _left_xf = ((xf / np.max(xf)) * (SCREEN_WIDTH / 2)).astype(float)
    indices = np.argsort(_left_xf)
    left_xf = _left_xf[indices]
    right_xf = (SCREEN_WIDTH - left_xf).astype(float)
    left_yf = np.abs(fft(left_channel)).astype(float)[indices]
    right_yf = np.abs(fft(right_channel)).astype(float)[indices]

    chunk_size = 3
    chunked_left_xf = avg_chunk_array(left_xf, chunk_size)
    chunked_right_xf = avg_chunk_array(right_xf, chunk_size)
    chunked_left_yf = avg_chunk_array(left_yf, chunk_size)
    chunked_right_yf = avg_chunk_array(right_yf, chunk_size)

    left_points = []
    right_points = []
    tips = []
    # TODO: Check how to mask lines so I can get the same effect as desktop, may end up being more intensive to draw tips
    for frame in range(len(chunked_left_xf)):
        left_points.append(
            [
                (
                    chunked_left_xf[frame],
                    (SCREEN_HEIGHT / 2) - (chunked_left_yf[frame] * 2) * 0.9,
                ),  # Bar starting point
                (
                    chunked_left_xf[frame],
                    (SCREEN_HEIGHT / 2) + (chunked_left_yf[frame] * 2) * 0.9,
                ),  # Bar ending point
            ]
        )
        right_points.append(
            [
                (
                    chunked_right_xf[frame],
                    (SCREEN_HEIGHT / 2) - (chunked_right_yf[frame] * 2) * 0.9,
                ),  # Bar starting point
                (
                    chunked_right_xf[frame],
                    (SCREEN_HEIGHT / 2) + (chunked_right_yf[frame] * 2) * 0.9,
                ),  # Bar ending point
            ]
        )
        tips.extend(
            (
                [
                    (
                        chunked_left_xf[frame],
                        (SCREEN_HEIGHT / 2) - (chunked_left_yf[frame] * 2),
                    ),  # Bar starting point
                    (
                        chunked_left_xf[frame],
                        (SCREEN_HEIGHT / 2) + (chunked_left_yf[frame] * 2),
                    ),  # Bar ending point
                ],
                [
                    (
                        chunked_right_xf[frame],
                        (SCREEN_HEIGHT / 2) - (chunked_right_yf[frame] * 2),
                    ),  # Bar starting point
                    (
                        chunked_right_xf[frame],
                        (SCREEN_HEIGHT / 2) + (chunked_right_yf[frame] * 2),
                    ),  # Bar ending point
                ],
            )
        )
    return left_points, right_points, tips


def update_visualizer_data(
    indata: np.ndarray, frames: int, _, status: sd.CallbackFlags
):
    # TODO: Clean this up
    if status:
        print(status)

    visualizer_surface.blit(background_entity, background_rect)

    left_points, right_points, tips = generate_waveform_points(indata, frames)
    for point_pair in tips:
        pygame.draw.line(visualizer_surface, (255, 0, 0), *point_pair)

    for point_pair in (*left_points, *right_points):
        pygame.draw.line(visualizer_surface, (255, 255, 255), *point_pair)

    # Drawing the raw data in points, as polygon
    # points = [(10 + left_xf[i] / 40, 300 - left_yf[i] / 30000) for i in range(1000)]

    # points = [(10 + left_xf[i] / 40, 300 - left_yf[i] / 30000) for i in range(300)]
    # points.append((max(left_xf) / 40, 300))
    # points.append((0, 300))
    #
    # pygame.draw.polygon(visualizer_surface, (100, 100, 255), points)
    # pygame.draw.lines(visualizer_surface, (255, 255, 255), False, (
    #     [(0, SCREEN_HEIGHT / 2), (0, (SCREEN_HEIGHT / 2) - 10)],
    #     [(2, SCREEN_HEIGHT / 2), (2, (SCREEN_HEIGHT / 2) - 15)]
    # ))

    # # Drawing the bars, which each are the average of five points
    # for i in range(200):
    #     val = 0
    #     for j in range(5):
    #         val += yf[i + j] / 30000
    #     val /= 5
    #     pygame.draw.line(visualizer_surface, (255, 255, 255), (10 + xf[i] / 8, 200 - val), (10 + xf[i] / 8, 200), 2)
    #
    # # Drawing the bars but in a circle
    # for i in range(200):
    #     val = 0
    #     for j in range(5):
    #         val += yf[i + j] / 30000
    #     val /= 5
    #
    #     ag = xf[i] * math.pi / 2000
    #
    #     module_ = val
    #
    #     pygame.draw.line(visualizer_surface, (255, 255, 255), (300 + math.cos(ag) * 50, 400 + math.sin(ag) * 50),
    #                  (300 + math.cos(ag) * (50 + val), 400 + math.sin(ag) * (50 + val)), 1)
    #
    # # Drawing the sound line
    # start += int(INPUT_SAMPLERATE / 600)

    # last_pos = (0, 0)
    # for i in range(1000):
    #     pos = (i * 6, y_origin - indata[:,0][
    #         (i + start) * 10] / 400)  # pos_y is every 10th value, divided by 90 to fit
    #     pygame.draw.line(visualizer_surface, (255, 255, 255), pos, last_pos, 1)
    #
    #     last_pos = pos


async def update_music_data(session: Session):
    global thumbnail_entity, title_entity, title_rect, artist_entity
    try:
        # If we don't have a music manager, rely on native Windows
        if not music_mgr:
            raise KeyError

        title, artists, thumbnail = await music_mgr.get_current_song()
        img = Image.open(thumbnail).convert("RGBA")
        thumbnail_entity = pygame.image.frombytes(
            img.tobytes(), img.size, "RGBA"
        ).convert()
    except KeyError:
        title, artists = await get_media_session_data(session)
        thumbnail_entity = pygame.image.load(settings["backup_thumb_path"])

    thumbnail_entity = pygame.transform.scale(
        thumbnail_entity, (THUMBNAIL_SIZE, THUMBNAIL_SIZE)
    )
    wrapped_title = textwrap.fill(title, 30)
    title_entity = title_font.render(wrapped_title, True, (255, 255, 255))
    title_rect = title_entity.get_rect().move(
        THUMBNAIL_SIZE * 1.55,
        SCREEN_HEIGHT - (THUMBNAIL_SIZE * 0.65) - title_entity.get_height(),
    )
    artist_entity = artist_font.render(", ".join(artists), True, (175, 175, 175))

    update_timeline(session)


def update_playback_data(session: Session):
    playback_status = get_media_playback_status(session)

    if playback_status == PlaybackStatus.PAUSED:
        pygame.time.set_timer(timer, 0)

    if playback_status == PlaybackStatus.PLAYING:
        update_timeline(session)
        pygame.time.set_timer(timer, 1000)


def update_timeline(session: Session):
    global CURRENT_SONG_TIME, TOTAL_SONG_TIME
    current_timedelta, total_timedelta = get_media_timeline_data(session)

    if TOTAL_SONG_TIME != total_timedelta:
        TOTAL_SONG_TIME = total_timedelta

    if CURRENT_SONG_TIME != current_timedelta:
        CURRENT_SONG_TIME = current_timedelta


def draw_timeline():
    global current_time_entity, total_time_entity
    current_time_entity = time_font.render(
        get_human_timestamp_from_timedelta(CURRENT_SONG_TIME),
        True,
        (255, 255, 255),
    )
    total_time_entity = time_font.render(
        get_human_timestamp_from_timedelta(TOTAL_SONG_TIME),
        True,
        (255, 255, 255),
    )
    timeline_surface.fill((0, 0, 0, 0))
    pygame.draw.line(  # Draw back line
        timeline_surface,
        (100, 100, 100),
        (TIMELINE_HEIGHT // 2, (TIMELINE_HEIGHT // 2) - 1),
        (
            timeline_surface.get_width() - (TIMELINE_HEIGHT // 2),
            (TIMELINE_HEIGHT // 2) - 1,
        ),
        width=TIMELINE_HEIGHT,
    )
    pygame.draw.circle(  # Draw end circle
        timeline_surface,
        (100, 100, 100),
        (timeline_surface.get_width() - (TIMELINE_HEIGHT // 2), (TIMELINE_HEIGHT // 2)),
        radius=TIMELINE_HEIGHT // 2,
    )
    pygame.draw.circle(  # Draw near circle in white
        timeline_surface,
        (255, 255, 255),
        (TIMELINE_HEIGHT // 2, TIMELINE_HEIGHT // 2),
        radius=TIMELINE_HEIGHT // 2,
    )
    current_song_spacing = (
        CURRENT_SONG_TIME.total_seconds() / TOTAL_SONG_TIME.total_seconds()
    ) * (timeline_surface.get_width() - TIMELINE_HEIGHT)
    # noinspection PyTypeChecker
    pygame.draw.line(  # Draw current song line
        timeline_surface,
        (255, 255, 255),
        ((TIMELINE_HEIGHT // 2 + 1), (TIMELINE_HEIGHT // 2) - 1),
        (
            max(
                TIMELINE_HEIGHT // 2, current_song_spacing - (TIMELINE_HEIGHT // 2) + 1
            ),
            (TIMELINE_HEIGHT // 2) - 1,
        ),
        width=TIMELINE_HEIGHT,
    )
    # noinspection PyTypeChecker
    pygame.draw.circle(  # Draw current tip circle
        timeline_surface,
        (255, 255, 255),
        (max(TIMELINE_HEIGHT // 2, current_song_spacing), TIMELINE_HEIGHT // 2),
        radius=TIMELINE_HEIGHT // 2,
    )


async def main():
    music_pid = get_music_process_pid(MUSIC_PROGRAM_NAME)
    if not music_pid:
        raise Exception("Could not find music PID")

    speakers_id = await get_device_id_from_name(CABLE_SPEAKER["name"])
    mic_id = await get_device_id_from_name(CABLE_MIC["name"])
    with listen_to_input_device(mic_id, None), switch_output_device_for_process(
        music_pid, speakers_id
    ):
        music_session = await get_music_session(MUSIC_PROGRAM_NAME)
        media_properties_changed = lambda x, _: asyncio.run(update_music_data(x))
        playback_info_changed = lambda x, _: update_playback_data(x)
        timeline_changed = lambda x, _: update_timeline(x)
        music_session.add_media_properties_changed(media_properties_changed)
        music_session.add_playback_info_changed(playback_info_changed)
        music_session.add_timeline_properties_changed(timeline_changed)
        await update_music_data(music_session)
        draw_timeline()

        global total_time_entity, current_time_entity, CURRENT_SONG_TIME
        with sd.InputStream(
            device=CABLE_MIC.get("index"),
            channels=CHANNELS,
            callback=update_visualizer_data,
        ):
            running = True
            while running:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False

                    if event.type == timer and CURRENT_SONG_TIME:
                        draw_timeline()
                        CURRENT_SONG_TIME = CURRENT_SONG_TIME + datetime.timedelta(
                            seconds=1
                        )

                screen.fill((0, 0, 0))
                try:
                    screen.blit(visualizer_surface, visualizer_surface_rect)
                    screen.blit(thumbnail_entity, thumbnail_rect)
                    screen.blit(title_entity, title_rect)
                    screen.blit(artist_entity, artist_rect)
                    screen.blit(timeline_surface, timeline_surface_rect)
                    screen.blit(current_time_entity, current_time_rect)
                    screen.blit(total_time_entity, total_time_rect)
                except pygame.error:
                    pass
                pygame.display.update()

            pygame.quit()


if __name__ == "__main__":
    asyncio.run(main())
