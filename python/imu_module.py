#!/usr/bin/env python3
"""
IMU Module for Project Diya – Request-Response version.
Fetches heading data from MCU only when needed.
"""

from arduino.app_utils import Bridge

class IMUReader:
    def __init__(self):
        # No handler registration – we call the MCU directly
        print("[IMU] Ready (request-response mode).")

    def get_heading(self):
        """Request current heading from the MCU."""
        try:
            heading = Bridge.call("get_heading")
            return float(heading)
        except Exception as e:
            print(f"[IMU] Error reading heading: {e}")
            return 0.0

    def get_relative_heading(self, reference):
        """
        Return heading relative to a reference heading in degrees (-180 to 180).
        Positive = clockwise (right), negative = counter-clockwise (left).
        """
        current = self.get_heading()
        diff = current - reference
        if diff > 180:
            diff -= 360
        elif diff < -180:
            diff += 360
        return diff

    def get_accelerometer(self):
        """Request accelerometer data from the MCU."""
        try:
            # You'll need to add Bridge.provide("get_accel") in sketch.ino
            accel = Bridge.call("get_accel")
            #print(f"[IMU Debug] Raw accel string: {accel}")
            return tuple(float(x) for x in accel.split(','))
        except Exception as e:
            print(f"[IMU] Error reading accelerometer: {e}")
            return (0.0, 0.0, 9.8)