# Keep JNI native bridge and callback receivers
-keep class kt.sanatani.wapower.jni.NeonizeBridge { *; }
-keep class kt.sanatani.wapower.jni.CallBridge { *; }
-keep class * extends com.google.protobuf.GeneratedMessageLite { *; }
