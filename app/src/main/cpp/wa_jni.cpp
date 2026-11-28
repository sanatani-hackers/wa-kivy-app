#include <android/log.h>
#include <dlfcn.h>
#include <jni.h>

#include <cstdint>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

#define LOG_TAG "WA_JNI"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

namespace {

struct BytesReturn {
    char* data;
    size_t size;
};

struct ProxySettings {
    char* proxyAddress;
    bool noWebsocket;
    bool onlyLogin;
    bool noMedia;
};

using FnQrOrStatusCb = void (*)(char*, char*);
using FnEventBytesCb = void (*)(const char*, char*, size_t, int);
using FnLogBytesCb = void (*)(const char*, size_t);
using FnCallEventCb = void (*)(char*, char*);

// Function pointer typedefs matching libneonize.so C exports
using FnGetVersion = char* (*)();
using FnFreeString = void (*)(char*);
using FnFreeBytesStruct = void (*)(BytesReturn*);

using FnNeonize = char* (*)(
    const char* db,
    const char* id,
    const unsigned char* jidByte,
    int jidSize,
    int newDevice,
    const char* logLevel,
    FnQrOrStatusCb qrCb,
    FnQrOrStatusCb logStatus,
    FnEventBytesCb eventCb,
    FnLogBytesCb logCb,
    const unsigned char* subscribes,
    int lenSubscriber,
    const unsigned char* devicePropsBuf,
    int devicePropsSize,
    const unsigned char* pairphone,
    int pairphoneSize,
    ProxySettings* proxySettings
);

using FnStop = void (*)(const char*);
using FnStopAll = void (*)();
using FnDisconnect = void (*)(const char*);
using FnLogout = char* (*)(const char*);
using FnIsConnected = bool (*)(const char*);
using FnIsLoggedIn = bool (*)(const char*);
using FnPairPhone = BytesReturn* (*)(const char*, const unsigned char*, int);
using FnGetAllDevices = char* (*)(const char*, FnLogBytesCb);
using FnGetMe = BytesReturn* (*)(const char*);
using FnSendMessage = BytesReturn* (*)(const char*, const unsigned char*, int, const unsigned char*, int);
using FnSendPresence = char* (*)(const char*, const char*);
using FnSendChatPresence = char* (*)(const char*, const unsigned char*, int, int, int);
using FnSetPassive = char* (*)(const char*, bool);
using FnMarkRead = char* (*)(const char*, const char*, int, const unsigned char*, int, const unsigned char*, int, const char*);

using FnCallRegisterCallback = char* (*)(FnCallEventCb);
using FnCallPlace = char* (*)(const char*, const char*, int);
using FnCallIdAction = char* (*)(const char*, const char*);
using FnCallPlayAudio = char* (*)(const char*, const char*, const char*, int);
using FnCallPlayPCMStream = char* (*)(const char*, const char*, const char*, int);
using FnCallPlayAV = char* (*)(const char*, const char*, const char*, const char*, int, int);
using FnCallPlayAVStream = char* (*)(const char*, const char*, const char*, int, int);
using FnCallRecordOrReact = char* (*)(const char*, const char*, const char*);
using FnCallListActive = char* (*)(const char*);

struct NeonizeApi {
    void* handle = nullptr;
    FnGetVersion GetVersion = nullptr;
    FnFreeString FreeString = nullptr;
    FnFreeBytesStruct FreeBytesStruct = nullptr;
    FnNeonize Neonize = nullptr;
    FnStop Stop = nullptr;
    FnStopAll StopAll = nullptr;
    FnDisconnect Disconnect = nullptr;
    FnLogout Logout = nullptr;
    FnIsConnected IsConnected = nullptr;
    FnIsLoggedIn IsLoggedIn = nullptr;
    FnPairPhone PairPhone = nullptr;
    FnGetAllDevices GetAllDevices = nullptr;
    FnGetMe GetMe = nullptr;
    FnSendMessage SendMessage = nullptr;
    FnSendPresence SendPresence = nullptr;
    FnSendChatPresence SendChatPresence = nullptr;
    FnSetPassive SetPassive = nullptr;
    FnMarkRead MarkRead = nullptr;

    FnCallRegisterCallback CallRegisterCallback = nullptr;
    FnCallPlace CallPlace = nullptr;
    FnCallIdAction CallAnswer = nullptr;
    FnCallIdAction CallDecline = nullptr;
    FnCallIdAction CallHangup = nullptr;
    FnCallPlayAudio CallPlayAudio = nullptr;
    FnCallPlayPCMStream CallPlayPCMStream = nullptr;
    FnCallIdAction CallPauseAudio = nullptr;
    FnCallIdAction CallResumeAudio = nullptr;
    FnCallIdAction CallStopAudio = nullptr;
    FnCallPlayAV CallPlayAV = nullptr;
    FnCallPlayAVStream CallPlayAVStream = nullptr;
    FnCallIdAction CallStopVideoStream = nullptr;
    FnCallIdAction CallStartVideo = nullptr;
    FnCallIdAction CallAcceptVideo = nullptr;
    FnCallIdAction CallStopVideo = nullptr;
    FnCallRecordOrReact CallRecordAudio = nullptr;
    FnCallRecordOrReact CallRecordVideo = nullptr;
    FnCallRecordOrReact CallSendReaction = nullptr;
    FnCallListActive CallListActive = nullptr;
};

JavaVM* g_jvm = nullptr;
std::mutex g_initMutex;
NeonizeApi g_api;
jobject g_bridgeObj = nullptr;
jmethodID g_onQrMethod = nullptr;
jmethodID g_onLoginStatusMethod = nullptr;
jmethodID g_onEventMethod = nullptr;
jmethodID g_onLogMethod = nullptr;
jmethodID g_onCallEventMethod = nullptr;
bool g_callCbRegistered = false;

class ScopedJniEnv {
public:
    ScopedJniEnv() {
        if (!g_jvm) return;
        jint rc = g_jvm->GetEnv(reinterpret_cast<void**>(&env_), JNI_VERSION_1_6);
        if (rc == JNI_EDETACHED) {
            if (g_jvm->AttachCurrentThread(&env_, nullptr) == JNI_OK) {
                attached_ = true;
            } else {
                env_ = nullptr;
            }
        } else if (rc != JNI_OK) {
            env_ = nullptr;
        }
    }

    ~ScopedJniEnv() {
        if (attached_ && g_jvm) {
            g_jvm->DetachCurrentThread();
        }
    }

    JNIEnv* get() const { return env_; }

private:
    JNIEnv* env_ = nullptr;
    bool attached_ = false;
};

// Convert arbitrary UTF-8 (including 4-byte emojis) into UTF-16 jstring without risking CheckJNI CESU-8 aborts.
jstring utf8ToJString(JNIEnv* env, const char* utf8) {
    if (!env || !utf8) return nullptr;
    const auto* bytes = reinterpret_cast<const uint8_t*>(utf8);
    size_t len = std::strlen(utf8);
    std::vector<jchar> out;
    out.reserve(len);

    size_t i = 0;
    while (i < len) {
        uint32_t cp = 0;
        uint8_t c = bytes[i];
        if (c < 0x80) {
            cp = c;
            i += 1;
        } else if ((c & 0xE0) == 0xC0 && i + 1 < len) {
            cp = ((c & 0x1F) << 6) | (bytes[i + 1] & 0x3F);
            i += 2;
        } else if ((c & 0xF0) == 0xE0 && i + 2 < len) {
            cp = ((c & 0x0F) << 12) | ((bytes[i + 1] & 0x3F) << 6) | (bytes[i + 2] & 0x3F);
            i += 3;
        } else if ((c & 0xF8) == 0xF0 && i + 3 < len) {
            cp = ((c & 0x07) << 18) | ((bytes[i + 1] & 0x3F) << 12) |
                 ((bytes[i + 2] & 0x3F) << 6) | (bytes[i + 3] & 0x3F);
            i += 4;
        } else {
            cp = 0xFFFD;
            i += 1;
        }

        if (cp <= 0xFFFF) {
            out.push_back(static_cast<jchar>(cp));
        } else {
            cp -= 0x10000;
            out.push_back(static_cast<jchar>(0xD800 + (cp >> 10)));
            out.push_back(static_cast<jchar>(0xDC00 + (cp & 0x3FF)));
        }
    }
    return env->NewString(out.data(), static_cast<jsize>(out.size()));
}

jstring consumeCString(JNIEnv* env, char* ptr) {
    if (!ptr) return nullptr;
    jstring result = utf8ToJString(env, ptr);
    if (g_api.FreeString) {
        g_api.FreeString(ptr);
    }
    return result;
}

jbyteArray consumeBytesReturn(JNIEnv* env, BytesReturn* br) {
    if (!br) return nullptr;
    jbyteArray arr = nullptr;
    if (br->data && br->size > 0) {
        arr = env->NewByteArray(static_cast<jsize>(br->size));
        if (arr) {
            env->SetByteArrayRegion(arr, 0, static_cast<jsize>(br->size), reinterpret_cast<const jbyte*>(br->data));
        }
    } else {
        arr = env->NewByteArray(0);
    }
    if (g_api.FreeBytesStruct) {
        g_api.FreeBytesStruct(br);
    }
    return arr;
}

std::string jstringToStdString(JNIEnv* env, jstring s) {
    if (!s) return {};
    const char* utf = env->GetStringUTFChars(s, nullptr);
    if (!utf) return {};
    std::string out(utf);
    env->ReleaseStringUTFChars(s, utf);
    return out;
}

std::vector<uint8_t> jbyteArrayToVec(JNIEnv* env, jbyteArray arr) {
    if (!arr) return {};
    jsize n = env->GetArrayLength(arr);
    if (n <= 0) return {};
    std::vector<uint8_t> buf(static_cast<size_t>(n));
    env->GetByteArrayRegion(arr, 0, n, reinterpret_cast<jbyte*>(buf.data()));
    return buf;
}

void clearIfException(JNIEnv* env, const char* where) {
    if (env && env->ExceptionCheck()) {
        LOGE("Unhandled Java exception in %s", where);
        env->ExceptionDescribe();
        env->ExceptionClear();
    }
}

void onGoQrCallback(char* uuid, char* qrCode) {
    ScopedJniEnv scoped;
    JNIEnv* env = scoped.get();
    if (!env || !g_bridgeObj || !g_onQrMethod) return;

    jstring jUuid = utf8ToJString(env, uuid ? uuid : "");
    jstring jQr = utf8ToJString(env, qrCode ? qrCode : "");
    env->CallVoidMethod(g_bridgeObj, g_onQrMethod, jUuid, jQr);
    clearIfException(env, "onGoQrCallback");
    if (jUuid) env->DeleteLocalRef(jUuid);
    if (jQr) env->DeleteLocalRef(jQr);
}

void onGoLoginStatusCallback(char* uuid, char* status) {
    ScopedJniEnv scoped;
    JNIEnv* env = scoped.get();
    if (!env || !g_bridgeObj || !g_onLoginStatusMethod) return;

    jstring jUuid = utf8ToJString(env, uuid ? uuid : "");
    jstring jStatus = utf8ToJString(env, status ? status : "");
    env->CallVoidMethod(g_bridgeObj, g_onLoginStatusMethod, jUuid, jStatus);
    clearIfException(env, "onGoLoginStatusCallback");
    if (jUuid) env->DeleteLocalRef(jUuid);
    if (jStatus) env->DeleteLocalRef(jStatus);
}

void onGoEventBytesCallback(const char* uuid, char* data, size_t size, int code) {
    ScopedJniEnv scoped;
    JNIEnv* env = scoped.get();
    if (!env || !g_bridgeObj || !g_onEventMethod) return;

    jstring jUuid = utf8ToJString(env, uuid ? uuid : "");
    jbyteArray jData = env->NewByteArray(static_cast<jsize>(size));
    if (jData && data && size > 0) {
        env->SetByteArrayRegion(jData, 0, static_cast<jsize>(size), reinterpret_cast<const jbyte*>(data));
    }
    env->CallVoidMethod(g_bridgeObj, g_onEventMethod, jUuid, jData, static_cast<jint>(code));
    clearIfException(env, "onGoEventBytesCallback");
    if (jUuid) env->DeleteLocalRef(jUuid);
    if (jData) env->DeleteLocalRef(jData);
}

void onGoLogBytesCallback(const char* data, size_t size) {
    ScopedJniEnv scoped;
    JNIEnv* env = scoped.get();
    if (!env || !g_bridgeObj || !g_onLogMethod) return;

    jbyteArray jData = env->NewByteArray(static_cast<jsize>(size));
    if (jData && data && size > 0) {
        env->SetByteArrayRegion(jData, 0, static_cast<jsize>(size), reinterpret_cast<const jbyte*>(data));
    }
    env->CallVoidMethod(g_bridgeObj, g_onLogMethod, jData);
    clearIfException(env, "onGoLogBytesCallback");
    if (jData) env->DeleteLocalRef(jData);
}

void onGoCallEventCallback(char* uuid, char* json) {
    ScopedJniEnv scoped;
    JNIEnv* env = scoped.get();
    if (!env || !g_bridgeObj || !g_onCallEventMethod) return;

    jstring jUuid = utf8ToJString(env, uuid ? uuid : "");
    jstring jJson = utf8ToJString(env, json ? json : "{}");
    env->CallVoidMethod(g_bridgeObj, g_onCallEventMethod, jUuid, jJson);
    clearIfException(env, "onGoCallEventCallback");
    if (jUuid) env->DeleteLocalRef(jUuid);
    if (jJson) env->DeleteLocalRef(jJson);
}

template <typename T>
bool resolveSym(void* handle, const char* name, T& target) {
    target = reinterpret_cast<T>(dlsym(handle, name));
    if (!target) {
        LOGE("Missing symbol in libneonize.so: %s (%s)", name, dlerror());
        return false;
    }
    return true;
}

bool ensureApiLoaded() {
    if (g_api.handle) return true;
    void* handle = dlopen("libneonize.so", RTLD_NOW | RTLD_GLOBAL);
    if (!handle) {
        LOGE("dlopen(libneonize.so) failed: %s", dlerror());
        return false;
    }
    bool ok = true;
    ok &= resolveSym(handle, "GetVersion", g_api.GetVersion);
    ok &= resolveSym(handle, "FreeString", g_api.FreeString);
    ok &= resolveSym(handle, "FreeBytesStruct", g_api.FreeBytesStruct);
    ok &= resolveSym(handle, "Neonize", g_api.Neonize);
    ok &= resolveSym(handle, "Stop", g_api.Stop);
    ok &= resolveSym(handle, "StopAll", g_api.StopAll);
    ok &= resolveSym(handle, "Disconnect", g_api.Disconnect);
    ok &= resolveSym(handle, "Logout", g_api.Logout);
    ok &= resolveSym(handle, "IsConnected", g_api.IsConnected);
    ok &= resolveSym(handle, "IsLoggedIn", g_api.IsLoggedIn);
    ok &= resolveSym(handle, "PairPhone", g_api.PairPhone);
    ok &= resolveSym(handle, "GetAllDevices", g_api.GetAllDevices);
    ok &= resolveSym(handle, "GetMe", g_api.GetMe);
    ok &= resolveSym(handle, "SendMessage", g_api.SendMessage);
    ok &= resolveSym(handle, "SendPresence", g_api.SendPresence);
    ok &= resolveSym(handle, "SendChatPresence", g_api.SendChatPresence);
    ok &= resolveSym(handle, "SetPassive", g_api.SetPassive);
    ok &= resolveSym(handle, "MarkRead", g_api.MarkRead);

    ok &= resolveSym(handle, "CallRegisterCallback", g_api.CallRegisterCallback);
    ok &= resolveSym(handle, "CallPlace", g_api.CallPlace);
    ok &= resolveSym(handle, "CallAnswer", g_api.CallAnswer);
    ok &= resolveSym(handle, "CallDecline", g_api.CallDecline);
    ok &= resolveSym(handle, "CallHangup", g_api.CallHangup);
    ok &= resolveSym(handle, "CallPlayAudio", g_api.CallPlayAudio);
    ok &= resolveSym(handle, "CallPlayPCMStream", g_api.CallPlayPCMStream);
    ok &= resolveSym(handle, "CallPauseAudio", g_api.CallPauseAudio);
    ok &= resolveSym(handle, "CallResumeAudio", g_api.CallResumeAudio);
    ok &= resolveSym(handle, "CallStopAudio", g_api.CallStopAudio);
    ok &= resolveSym(handle, "CallPlayAV", g_api.CallPlayAV);
    ok &= resolveSym(handle, "CallPlayAVStream", g_api.CallPlayAVStream);
    ok &= resolveSym(handle, "CallStopVideoStream", g_api.CallStopVideoStream);
    ok &= resolveSym(handle, "CallStartVideo", g_api.CallStartVideo);
    ok &= resolveSym(handle, "CallAcceptVideo", g_api.CallAcceptVideo);
    ok &= resolveSym(handle, "CallStopVideo", g_api.CallStopVideo);
    ok &= resolveSym(handle, "CallRecordAudio", g_api.CallRecordAudio);
    ok &= resolveSym(handle, "CallRecordVideo", g_api.CallRecordVideo);
    ok &= resolveSym(handle, "CallSendReaction", g_api.CallSendReaction);
    ok &= resolveSym(handle, "CallListActive", g_api.CallListActive);

    if (!ok) {
        return false;
    }
    g_api.handle = handle;
    LOGI("Loaded libneonize.so symbols successfully");
    return true;
}

}  // namespace

extern "C" JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM* vm, void* /*reserved*/) {
    g_jvm = vm;
    return JNI_VERSION_1_6;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_kt_sanatani_wapower_jni_NeonizeBridge_nativeInit(JNIEnv* env, jobject /*thiz*/, jobject bridgeObj) {
    std::lock_guard<std::mutex> lock(g_initMutex);
    if (!ensureApiLoaded()) {
        return JNI_FALSE;
    }
    if (g_bridgeObj) {
        env->DeleteGlobalRef(g_bridgeObj);
        g_bridgeObj = nullptr;
    }
    g_bridgeObj = env->NewGlobalRef(bridgeObj);
    jclass cls = env->GetObjectClass(bridgeObj);
    g_onQrMethod = env->GetMethodID(cls, "onNativeQr", "(Ljava/lang/String;Ljava/lang/String;)V");
    g_onLoginStatusMethod = env->GetMethodID(cls, "onNativeLoginStatus", "(Ljava/lang/String;Ljava/lang/String;)V");
    g_onEventMethod = env->GetMethodID(cls, "onNativeEvent", "(Ljava/lang/String;[BI)V");
    g_onLogMethod = env->GetMethodID(cls, "onNativeLog", "([B)V");
    g_onCallEventMethod = env->GetMethodID(cls, "onNativeCallEvent", "(Ljava/lang/String;Ljava/lang/String;)V");
    env->DeleteLocalRef(cls);

    if (!g_onQrMethod || !g_onLoginStatusMethod || !g_onEventMethod || !g_onLogMethod || !g_onCallEventMethod) {
        LOGE("Failed resolving Kotlin callback methods on NeonizeBridge");
        return JNI_FALSE;
    }

    if (!g_callCbRegistered && g_api.CallRegisterCallback) {
        char* regRes = g_api.CallRegisterCallback(onGoCallEventCallback);
        if (regRes && g_api.FreeString) {
            g_api.FreeString(regRes);
        }
        g_callCbRegistered = true;
    }
    return JNI_TRUE;
}

extern "C" JNIEXPORT jstring JNICALL
Java_kt_sanatani_wapower_jni_NeonizeBridge_nativeGetVersion(JNIEnv* env, jobject /*thiz*/) {
    if (!ensureApiLoaded() || !g_api.GetVersion) return nullptr;
    return consumeCString(env, g_api.GetVersion());
}

extern "C" JNIEXPORT jstring JNICALL
Java_kt_sanatani_wapower_jni_NeonizeBridge_nativeStartNeonize(
    JNIEnv* env,
    jobject /*thiz*/,
    jstring dbPath,
    jstring uuid,
    jbyteArray jidBytes,
    jint newDevice,
    jstring logLevel,
    jbyteArray subscribers,
    jbyteArray devicePropsBytes,
    jbyteArray pairPhoneBytes
) {
    if (!ensureApiLoaded() || !g_api.Neonize) {
        return utf8ToJString(env, "libneonize.so not loaded");
    }
    std::string db = jstringToStdString(env, dbPath);
    std::string id = jstringToStdString(env, uuid);
    std::string lvl = jstringToStdString(env, logLevel);
    std::vector<uint8_t> jid = jbyteArrayToVec(env, jidBytes);
    std::vector<uint8_t> subs = jbyteArrayToVec(env, subscribers);
    std::vector<uint8_t> props = jbyteArrayToVec(env, devicePropsBytes);
    std::vector<uint8_t> pair = jbyteArrayToVec(env, pairPhoneBytes);

    char* err = g_api.Neonize(
        db.c_str(),
        id.c_str(),
        jid.empty() ? nullptr : jid.data(),
        static_cast<int>(jid.size()),
        static_cast<int>(newDevice),
        lvl.c_str(),
        onGoQrCallback,
        onGoLoginStatusCallback,
        onGoEventBytesCallback,
        onGoLogBytesCallback,
        subs.empty() ? nullptr : subs.data(),
        static_cast<int>(subs.size()),
        props.empty() ? nullptr : props.data(),
        static_cast<int>(props.size()),
        pair.empty() ? nullptr : pair.data(),
        static_cast<int>(pair.size()),
        nullptr
    );
    return consumeCString(env, err);
}

extern "C" JNIEXPORT void JNICALL
Java_kt_sanatani_wapower_jni_NeonizeBridge_nativeStop(JNIEnv* env, jobject /*thiz*/, jstring uuid) {
    if (!ensureApiLoaded() || !g_api.Stop) return;
    std::string id = jstringToStdString(env, uuid);
    g_api.Stop(id.c_str());
}

extern "C" JNIEXPORT void JNICALL
Java_kt_sanatani_wapower_jni_NeonizeBridge_nativeStopAll(JNIEnv* /*env*/, jobject /*thiz*/) {
    if (!ensureApiLoaded() || !g_api.StopAll) return;
    g_api.StopAll();
}

extern "C" JNIEXPORT void JNICALL
Java_kt_sanatani_wapower_jni_NeonizeBridge_nativeDisconnect(JNIEnv* env, jobject /*thiz*/, jstring uuid) {
    if (!ensureApiLoaded() || !g_api.Disconnect) return;
    std::string id = jstringToStdString(env, uuid);
    g_api.Disconnect(id.c_str());
}

extern "C" JNIEXPORT jstring JNICALL
Java_kt_sanatani_wapower_jni_NeonizeBridge_nativeLogout(JNIEnv* env, jobject /*thiz*/, jstring uuid) {
    if (!ensureApiLoaded() || !g_api.Logout) return utf8ToJString(env, "libneonize.so not loaded");
    std::string id = jstringToStdString(env, uuid);
    return consumeCString(env, g_api.Logout(id.c_str()));
}

extern "C" JNIEXPORT jboolean JNICALL
Java_kt_sanatani_wapower_jni_NeonizeBridge_nativeIsConnected(JNIEnv* env, jobject /*thiz*/, jstring uuid) {
    if (!ensureApiLoaded() || !g_api.IsConnected) return JNI_FALSE;
    std::string id = jstringToStdString(env, uuid);
    return g_api.IsConnected(id.c_str()) ? JNI_TRUE : JNI_FALSE;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_kt_sanatani_wapower_jni_NeonizeBridge_nativeIsLoggedIn(JNIEnv* env, jobject /*thiz*/, jstring uuid) {
    if (!ensureApiLoaded() || !g_api.IsLoggedIn) return JNI_FALSE;
    std::string id = jstringToStdString(env, uuid);
    return g_api.IsLoggedIn(id.c_str()) ? JNI_TRUE : JNI_FALSE;
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_kt_sanatani_wapower_jni_NeonizeBridge_nativePairPhone(
    JNIEnv* env,
    jobject /*thiz*/,
    jstring uuid,
    jbyteArray pairPhoneParamsBytes
) {
    if (!ensureApiLoaded() || !g_api.PairPhone) return nullptr;
    std::string id = jstringToStdString(env, uuid);
    std::vector<uint8_t> buf = jbyteArrayToVec(env, pairPhoneParamsBytes);
    BytesReturn* br = g_api.PairPhone(id.c_str(), buf.empty() ? nullptr : buf.data(), static_cast<int>(buf.size()));
    return consumeBytesReturn(env, br);
}

extern "C" JNIEXPORT jstring JNICALL
Java_kt_sanatani_wapower_jni_NeonizeBridge_nativeGetAllDevices(JNIEnv* env, jobject /*thiz*/, jstring dbPath) {
    if (!ensureApiLoaded() || !g_api.GetAllDevices) return nullptr;
    std::string db = jstringToStdString(env, dbPath);
    return consumeCString(env, g_api.GetAllDevices(db.c_str(), onGoLogBytesCallback));
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_kt_sanatani_wapower_jni_NeonizeBridge_nativeGetMe(JNIEnv* env, jobject /*thiz*/, jstring uuid) {
    if (!ensureApiLoaded() || !g_api.GetMe) return nullptr;
    std::string id = jstringToStdString(env, uuid);
    return consumeBytesReturn(env, g_api.GetMe(id.c_str()));
}

extern "C" JNIEXPORT jstring JNICALL
Java_kt_sanatani_wapower_jni_NeonizeBridge_nativeSendPresence(
    JNIEnv* env,
    jobject /*thiz*/,
    jstring uuid,
    jstring presence
) {
    if (!ensureApiLoaded() || !g_api.SendPresence) return utf8ToJString(env, "libneonize.so not loaded");
    std::string id = jstringToStdString(env, uuid);
    std::string pres = jstringToStdString(env, presence);
    return consumeCString(env, g_api.SendPresence(id.c_str(), pres.c_str()));
}

extern "C" JNIEXPORT jstring JNICALL
Java_kt_sanatani_wapower_jni_NeonizeBridge_nativeSetPassive(
    JNIEnv* env,
    jobject /*thiz*/,
    jstring uuid,
    jboolean passive
) {
    if (!ensureApiLoaded() || !g_api.SetPassive) return utf8ToJString(env, "libneonize.so not loaded");
    std::string id = jstringToStdString(env, uuid);
    return consumeCString(env, g_api.SetPassive(id.c_str(), passive == JNI_TRUE));
}

extern "C" JNIEXPORT jstring JNICALL
Java_kt_sanatani_wapower_jni_NeonizeBridge_nativeCallListActive(
    JNIEnv* env,
    jobject /*thiz*/,
    jstring uuid
) {
    if (!ensureApiLoaded() || !g_api.CallListActive) return utf8ToJString(env, "{\"ok\":false,\"error\":\"not loaded\"}");
    std::string id = jstringToStdString(env, uuid);
    return consumeCString(env, g_api.CallListActive(id.c_str()));
}
