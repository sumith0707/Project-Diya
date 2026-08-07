#!/usr/bin/env python3
"""
TTS Manager for Project Diya using Piper TTS.
"""

import os
import subprocess
import threading
import tempfile

class TTSManager:
    def __init__(self, piper_bin="piper/piper", 
                 model_path="piper_voices/en_US-lessac-medium.onnx",
                 audio_device="plughw:1,0"):
        """
        Initialize TTS Manager with Piper.
        
        :param piper_bin: Path to the Piper binary (relative or absolute)
        :param model_path: Path to the Piper voice model (.onnx)
        :param audio_device: ALSA device for aplay (e.g., "plughw:1,0")
        """
        self.piper_bin = piper_bin
        self.model_path = model_path
        self.audio_device = audio_device
        self._is_speaking = False
        self._lock = threading.Lock()

    def speak(self, text, async_mode=True):
        """Speak text using Piper TTS."""
        if not text or not text.strip():
            return

        with self._lock:
            self._is_speaking = True

        if async_mode:
            thread = threading.Thread(target=self._speak_sync, args=(text,))
            thread.daemon = True
            thread.start()
        else:
            self._speak_sync(text)

    def _speak_sync(self, text):
        """Synchronous speech (runs in background thread)."""
        try:
            # Use temp file for WAV
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_path = f.name

            # Generate WAV using Piper
            cmd_gen = [
                self.piper_bin,
                "--model", self.model_path,
                "--output_file", temp_path
            ]
            proc_gen = subprocess.Popen(cmd_gen, stdin=subprocess.PIPE, 
                                        stdout=subprocess.PIPE, 
                                        stderr=subprocess.PIPE)
            proc_gen.stdin.write(text.encode('utf-8'))
            proc_gen.stdin.close()
            proc_gen.wait()

            if proc_gen.returncode != 0:
                stderr = proc_gen.stderr.read().decode()
                print(f"[TTS] Piper error: {stderr}")
                # Try to play if any audio was generated
                if not os.path.exists(temp_path):
                    return

            # Play WAV using aplay
            cmd_play = [
                "aplay",
                "-D", self.audio_device,
                temp_path
            ]
            subprocess.run(cmd_play, check=True, capture_output=True)

            # Clean up
            os.unlink(temp_path)

        except Exception as e:
            print(f"[TTS] Error: {e}")
        finally:
            with self._lock:
                self._is_speaking = False

    def speak_async(self, text):
        """Alias for speak(text, async_mode=True)."""
        self.speak(text, async_mode=True)

    def speak_sync(self, text):
        """Alias for speak(text, async_mode=False)."""
        self.speak(text, async_mode=False)

    def stop(self):
        """Stop current speech."""
        os.system("pkill piper")
        os.system("pkill aplay")
        with self._lock:
            self._is_speaking = False
        print("[TTS] Stopped.")