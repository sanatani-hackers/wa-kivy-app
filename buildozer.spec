[app]
title = WA Core
package.name = waapp
package.domain = org.sanatani
source.dir = .
source.include_exts = py,png,jpg,kv,json,so
version = 0.1.0
orientation = portrait

requirements = python3,kivy==2.3.0,oscpy,pyjnius,android,protobuf,requests,phonenumbers,segno
p4a.local_recipes = ./recipes
p4a.branch = v2024.01.21

# Separate process, foreground notification, restarted if killed
services = whatsapp:service/main.py:foreground:sticky

android.permissions = INTERNET,FOREGROUND_SERVICE,FOREGROUND_SERVICE_DATA_SYNC,POST_NOTIFICATIONS,REQUEST_IGNORE_BATTERY_OPTIMIZATIONS
# targetSdk 33 avoids the Android 14 "foregroundServiceType" manifest requirement (targetSdk >= 34)
android.api = 33
android.minapi = 26
android.archs = arm64-v8a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
