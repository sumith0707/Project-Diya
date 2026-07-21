#!/usr/bin/env python3
"""
Voice Recognition Module using Faster Whisper (tiny) with WebRTC VAD.
Includes fuzzy matching to map misrecognized place names.
"""

import os
import json
import queue
import threading
import time
import difflib
import sounddevice as sd
import numpy as np
import webrtcvad
from faster_whisper import WhisperModel

class VoiceRecognition:
    def __init__(self, model_name="tiny", device="cpu", compute_type="int8",
                 sample_rate=16000, block_duration_ms=30,
                 silence_timeout=1.5, vad_mode=1):
        """
        Initialize the Faster Whisper recognizer with WebRTC VAD.

        :param model_name: Whisper model size ("tiny", "base", "small", etc.)
        :param device: "cpu" or "cuda"
        :param compute_type: "int8", "float16", "float32"
        :param sample_rate: Audio sample rate (must be 16kHz)
        :param block_duration_ms: VAD block size (10, 20, or 30ms)
        :param silence_timeout: Seconds of silence before auto‑stop
        :param vad_mode: WebRTC VAD aggressiveness (0=least, 3=most)
        """
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.sample_rate = sample_rate
        self.sample_duration_ms = block_duration_ms
        self.silence_timeout = silence_timeout
        self.vad_mode = vad_mode

        # VAD requires specific block sizes
        self.block_size = int(sample_rate * block_duration_ms / 1000)
        if self.block_size not in [160, 320, 480]:
            raise ValueError(f"Invalid block size: {self.block_size}. Must be 160, 320, or 480 samples.")

        self._model = None
        self._vad = None
        self._audio_queue = queue.Queue()
        self._is_recording = False
        self._recording_thread = None
        self._result_callback = None
        self._stop_requested = False

        # ---------- Fuzzy Matching ----------
        # self._place_list = []
        # self._fuzzy_threshold = 0.6

        self._load_model()
        self._init_vad()
        # self._set_default_places()

    def _load_model(self):
        """Load Faster Whisper model."""
        try:
            self._model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type)
            print(f"[VoiceRecognition] Faster Whisper '{self.model_name}' model loaded.")
        except Exception as e:
            raise RuntimeError(f"Failed to load Faster Whisper model: {e}")

    def _init_vad(self):
        self._vad = webrtcvad.Vad(self.vad_mode)
        print(f"[VoiceRecognition] WebRTC VAD initialized (aggressiveness={self.vad_mode})")

    # def _set_default_places(self):
    #     """Set default place names for fuzzy matching."""
    #     self._place_list = [
    #         "mangalore", "kinnigoli", "udupi", "bengaluru", "mysore",
    #         "mumbai", "delhi", "goa", "kochi", "chennai", "hyderabad",
    #         "pune", "ahmedabad", "jaipur", "lucknow", "patna", "bhopal",
    #         "nagpur", "surat", "vadodara", "indore", "raipur", "ranchi",
    #         "bhubaneswar", "guwahati", "chandigarh"
    #     ]
    #     print(f"[VoiceRecognition] Default place list set with {len(self._place_list)} names.")

    # def set_place_list(self, place_names, threshold=0.6):
    #     self._place_list = [p.lower() for p in place_names]
    #     self._fuzzy_threshold = threshold
    #     print(f"[VoiceRecognition] Place list updated with {len(self._place_list)} names.")

    # def _fuzzy_match_place(self, text):
    #     if not self._place_list or not text:
    #         return text
    #     text_lower = text.lower()
    #     words = text_lower.split()
    #     best_match = None
    #     best_score = 0.0
    #     for word in words:
    #         if len(word) < 3:
    #             continue
    #         for place in self._place_list:
    #             score = difflib.SequenceMatcher(None, word, place).ratio()
    #             if score > best_score:
    #                 best_score = score
    #                 best_match = place
    #     if best_match and best_score >= self._fuzzy_threshold:
    #         return best_match.title()
    #     return text

    def _audio_callback(self, indata, frames, time, status):
        if self._is_recording:
            self._audio_queue.put(indata.copy())

    def _is_speech(self, audio_chunk):
        try:
            audio_int16 = (audio_chunk * 32767).astype(np.int16)
            audio_bytes = audio_int16.tobytes()
            return self._vad.is_speech(audio_bytes, self.sample_rate)
        except Exception as e:
            print(f"[VoiceRecognition] VAD error: {e}")
            return False

    def _recognition_loop(self):
        print("[VoiceRecognition] Recording started. Speak now...")
        self._is_recording = True

        silence_duration = 0.0
        frame_duration = self.block_size / self.sample_rate
        audio_buffer = bytearray()  # accumulate all audio

        with sd.InputStream(samplerate=self.sample_rate,
                            blocksize=self.block_size,
                            device=None,
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

        # After recording stops, transcribe the accumulated audio
        if len(audio_buffer) > 0:
            self._transcribe_audio(audio_buffer)
        else:
            print("[VoiceRecognition] No audio captured.")

        print("[VoiceRecognition] Recording stopped.")

    def _transcribe_audio(self, audio_buffer_bytes):
        """Convert bytes to float32 array and run Faster Whisper transcription."""
        try:
            # Convert bytes to int16 numpy array
            audio_int16 = np.frombuffer(audio_buffer_bytes, dtype=np.int16)
            # Convert to float32 normalized to [-1, 1]
            audio_float32 = audio_int16.astype(np.float32) / 32768.0

            # Transcribe with Faster Whisper
            segments, info = self._model.transcribe(audio_float32, language="en", beam_size=5)

            # Combine all segments into one text
            full_text = "".join([seg.text for seg in segments]).strip()

            if full_text:
                # Apply fuzzy matching
                # matched_text = self._fuzzy_match_place(full_text)
                # if matched_text != full_text:
                #     print(f"[VoiceRecognition] Fuzzy matched: '{full_text}' → '{matched_text}'")
                print(f"[VoiceRecognition] Recognized: {full_text}")
                # if self._result_callback:
                #     self._result_callback(matched_text)
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
        # Clear the queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

        self._recording_thread = threading.Thread(target=self._recognition_loop, daemon=True)
        self._recording_thread.start()

    def stop_recording(self):
        """Manually stop recording (e.g., on long button press)."""
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