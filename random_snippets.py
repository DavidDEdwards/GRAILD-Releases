"""Decode random snippets to WAV so seeking works for MP3, OGG, and WAV."""
import io
import random
import wave

import pygame


class RandomSnippet:
    def __init__(self, filename, longest_preview, *, start_frame=None):
        frequency, sample_format, channels = pygame.mixer.get_init()
        self.frequency = frequency
        self.channels = channels
        self.sample_width = abs(sample_format) // 8
        self.frame_width = self.sample_width * channels
        self.pcm = pygame.mixer.Sound(filename).get_raw()
        if sample_format == -8:
            self.pcm = bytes((sample + 128) % 256 for sample in self.pcm)
        self.frames = len(self.pcm) // self.frame_width
        if not self.frames:
            raise ValueError("The song has no playable audio.")
        reserved = min(self.frames, int(longest_preview * frequency))
        if start_frame is not None and (type(start_frame) is not int or not 0 <= start_frame <= self.frames - reserved):
            raise ValueError("The saved snippet no longer fits this audio file.")
        self.start_frame = random.randint(0, self.frames - reserved) if start_frame is None else start_frame

    def clip(self, duration):
        end_frame = min(self.frames, self.start_frame + max(1, int(duration * self.frequency)))
        audio = self.pcm[self.start_frame * self.frame_width:end_frame * self.frame_width]
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(self.channels)
            output.setsampwidth(self.sample_width)
            output.setframerate(self.frequency)
            output.writeframes(audio)
        buffer.seek(0)
        return buffer, (end_frame - self.start_frame) / self.frequency
