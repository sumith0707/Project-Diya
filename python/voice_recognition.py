#!/usr/bin/env python3
"""
Voice Recognition Module using Faster Whisper (tiny) with WebRTC VAD.
Includes resampling to 16 kHz.
"""

import os
import json
import queue
import threading
import time
import sounddevice as sd
import numpy as np
import webrtcvad
from faster_whisper import WhisperModel

# Try to import scipy for high-quality resampling
try:
    from scipy import signal
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    print("[VoiceRecognition] scipy not available – using numpy interpolation for resampling.")

class VoiceRecognition:
    def __init__(self, model_name="tiny", device="cpu", compute_type="int8",
                 sample_rate=16000, block_duration_ms=30,
                 silence_timeout=1.5, vad_mode=1,
                 audio_device="AB13X USB Audio"):
        """
        Initialize the Faster Whisper recognizer with WebRTC VAD.

        :param audio_device: Name or index of the audio input device.
        :param sample_rate: Target sample rate for Whisper (always 16 kHz).
        """
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.target_sample_rate = sample_rate  # Whisper expects 16 kHz
        self.audio_device = audio_device
        self.block_duration_ms = block_duration_ms
        self.silence_timeout = silence_timeout
        self.vad_mode = vad_mode

        # ---- Determine the device's native sample rate ----
        self.capture_sample_rate = self._get_device_sample_rate(audio_device)
        print(f"[VoiceRecognition] Using device sample rate: {self.capture_sample_rate} Hz")

        # VAD requires specific block sizes (10, 20, or 30 ms at capture rate)
        self.block_size = int(self.capture_sample_rate * block_duration_ms / 1000)
        if self.block_size not in [160, 320, 480]:
            # If block size doesn't match VAD requirements, adjust to 30 ms
            self.block_size = int(self.capture_sample_rate * 30 / 1000)

        self._model = None
        self._vad = None
        self._audio_queue = queue.Queue()
        self._is_recording = False
        self._recording_thread = None
        self._result_callback = None
        self._stop_requested = False

        self._load_model()
        self._init_vad()

    def _get_device_sample_rate(self, device):
        """Get the default sample rate of the audio device."""
        try:
            dev_info = sd.query_devices(device)
            # Most devices have a default_samplerate field
            if 'default_samplerate' in dev_info and dev_info['default_samplerate'] > 0:
                return int(dev_info['default_samplerate'])
            # Fallback: try 48000 (common for USB audio)
            return 48000
        except Exception as e:
            print(f"[VoiceRecognition] Could not query device: {e}. Using 48000 Hz.")
            return 48000

    def _resample(self, audio, orig_sr, target_sr):
        """Resample audio from orig_sr to target_sr."""
        if orig_sr == target_sr:
            return audio
        if SCIPY_AVAILABLE:
            # High-quality resampling with scipy
            number_of_samples = int(round(len(audio) * target_sr / orig_sr))
            resampled = signal.resample(audio, number_of_samples)
            return resampled.astype(np.float32)
        else:
            # Simple linear interpolation using numpy
            import numpy as np
            duration = len(audio) / orig_sr
            new_length = int(duration * target_sr)
            x_old = np.linspace(0, len(audio), len(audio))
            x_new = np.linspace(0, len(audio), new_length)
            resampled = np.interp(x_new, x_old, audio)
            return resampled.astype(np.float32)

    def _load_model(self):
        try:
            self._model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type)
            print(f"[VoiceRecognition] Faster Whisper '{self.model_name}' model loaded.")
        except Exception as e:
            raise RuntimeError(f"Failed to load Faster Whisper model: {e}")

    def _init_vad(self):
        self._vad = webrtcvad.Vad(self.vad_mode)
        print(f"[VoiceRecognition] WebRTC VAD initialized (aggressiveness={self.vad_mode})")

    def _audio_callback(self, indata, frames, time, status):
        if self._is_recording:
            self._audio_queue.put(indata.copy())

    def _is_speech(self, audio_chunk):
        try:
            # VAD expects 16-bit PCM at 16kHz, but we're capturing at device rate.
            # We need to resample the chunk to 16kHz for VAD.
            # For performance, we can downsample to 16kHz only for VAD.
            # But VAD is frame-based; we'll downsample each chunk.
            # However, we're using VAD on the captured frames; we should resample the chunk.
            # This is a bit of a shortcut: we'll assume the chunk is at capture_sample_rate,
            # resample it to 16kHz for VAD, and then also keep the original for transcription.
            # But we can also just use VAD on the original rate? Actually webrtcvad expects 16kHz.
            # So we need to resample the chunk to 16kHz.
            if self.capture_sample_rate != 16000:
                # Resample to 16kHz for VAD
                chunk_float = audio_chunk.astype(np.float32)
                resampled_float = self._resample(chunk_float.flatten(), self.capture_sample_rate, 16000)
                # Convert to int16
                audio_int16 = (resampled_float * 32767).astype(np.int16)
                audio_bytes = audio_int16.tobytes()
            else:
                audio_int16 = (audio_chunk * 32767).astype(np.int16)
                audio_bytes = audio_int16.tobytes()
            return self._vad.is_speech(audio_bytes, 16000)
        except Exception as e:
            print(f"[VoiceRecognition] VAD error: {e}")
            return False

    def _recognition_loop(self):
        print("[VoiceRecognition] Recording started. Speak now...")
        self._is_recording = True

        silence_duration = 0.0
        frame_duration = self.block_size / self.capture_sample_rate
        audio_buffer = bytearray()

        with sd.InputStream(samplerate=self.capture_sample_rate,
                            blocksize=self.block_size,
                            device=self.audio_device,
                            dtype='float32',
                            channels=1,
                            callback=self._audio_callback):
            while self._is_recording and not self._stop_requested:
                try:
                    chunk = self._audio_queue.get(timeout=0.1)
                except queue.Empty:
                    silence_duration += 0.1
                    if silence_duration >= self.silence_timeout:
                        print("[VoiceRecognition] Silence timeout – stopping.")
                        self._is_recording = False
                        break
                    continue

                if self._is_speech(chunk):
                    silence_duration = 0.0
                    # Convert float32 to int16 and add to buffer
                    audio_int16 = (chunk * 32767).astype(np.int16)
                    audio_buffer.extend(audio_int16.tobytes())
                else:
                    silence_duration += frame_duration
                    if silence_duration >= self.silence_timeout:
                        print("[VoiceRecognition] Silence timeout – stopping.")
                        self._is_recording = False
                        break

        if len(audio_buffer) > 0:
            self._transcribe_audio(audio_buffer)
        else:
            print("[VoiceRecognition] No audio captured.")

        print("[VoiceRecognition] Recording stopped.")

    def _transcribe_audio(self, audio_buffer_bytes):
        try:
            # Convert bytes to int16 numpy array (at capture_sample_rate)
            audio_int16 = np.frombuffer(audio_buffer_bytes, dtype=np.int16)
            # Convert to float32 normalized to [-1, 1]
            audio_float32 = audio_int16.astype(np.float32) / 32768.0

            # Resample to 16kHz for Whisper
            if self.capture_sample_rate != self.target_sample_rate:
                print(f"[VoiceRecognition] Resampling from {self.capture_sample_rate} Hz to {self.target_sample_rate} Hz")
                audio_float32 = self._resample(audio_float32, self.capture_sample_rate, self.target_sample_rate)

            segments, info = self._model.transcribe(audio_float32, language="en", beam_size=5)
            full_text = "".join([seg.text for seg in segments]).strip()

            if full_text:
                if self._result_callback:
                    self._result_callback(full_text)
                else:
                    print("[VoiceRecognition] WARNING: No callback set!")
            else:
                print("[VoiceRecognition] No speech recognized.")
        except Exception as e:
            print(f"[VoiceRecognition] Transcription error: {e}")

    def start_recording(self, callback=None):
        if self._recording_thread and self._recording_thread.is_alive():
            print("[VoiceRecognition] Already recording.")
            return

        self._result_callback = callback
        self._stop_requested = False
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

        self._recording_thread = threading.Thread(target=self._recognition_loop, daemon=True)
        self._recording_thread.start()

    def stop_recording(self):
        if threading.current_thread() == self._recording_thread:
            self._is_recording = False
            self._stop_requested = True
            return

        self._is_recording = False
        self._stop_requested = True
        if self._recording_thread and self._recording_thread.is_alive():
            self._recording_thread.join(timeout=1.0)
            self._recording_thread = None
        print("[VoiceRecognition] Recording stopped manually.")

    @property
    def is_recording(self):
        return self._is_recording