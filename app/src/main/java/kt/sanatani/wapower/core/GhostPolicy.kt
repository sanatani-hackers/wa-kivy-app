package kt.sanatani.wapower.core

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch

private val Context.ghostDataStore: DataStore<Preferences> by preferencesDataStore(name = "ghost_policy")

data class GhostConfig(
    val ghostRead: Boolean = false,
    val ghostPresence: Boolean = false,
    val ghostTyping: Boolean = false,
    val ghostViewOnceInline: Boolean = false,
    val autoWipe: Boolean = true,
    val antiDeleteRelay: Boolean = true,
)

class GhostGuard(private val configProvider: () -> GhostConfig) {
    fun allowMarkRead(): Boolean = !configProvider().ghostRead
    fun allowPresence(): Boolean = !configProvider().ghostPresence
    fun allowTyping(): Boolean = !configProvider().ghostTyping
    fun allowViewOnceReceipt(): Boolean = !configProvider().ghostViewOnceInline
}

class GhostPolicy(context: Context) {
    private val appContext = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val _config = MutableStateFlow(GhostConfig())
    val config: StateFlow<GhostConfig> = _config.asStateFlow()

    val guard = GhostGuard { _config.value }

    init {
        scope.launch {
            appContext.ghostDataStore.data
                .map { prefs ->
                    GhostConfig(
                        ghostRead = prefs[KEY_GHOST_READ] ?: false,
                        ghostPresence = prefs[KEY_GHOST_PRESENCE] ?: false,
                        ghostTyping = prefs[KEY_GHOST_TYPING] ?: false,
                        ghostViewOnceInline = prefs[KEY_GHOST_VIEW_ONCE] ?: false,
                        autoWipe = prefs[KEY_AUTO_WIPE] ?: true,
                        antiDeleteRelay = prefs[KEY_ANTI_DELETE] ?: true,
                    )
                }
                .collect { _config.value = it }
        }
    }

    suspend fun update(transform: (GhostConfig) -> GhostConfig) {
        val next = transform(_config.value)
        _config.value = next
        appContext.ghostDataStore.edit { prefs ->
            prefs[KEY_GHOST_READ] = next.ghostRead
            prefs[KEY_GHOST_PRESENCE] = next.ghostPresence
            prefs[KEY_GHOST_TYPING] = next.ghostTyping
            prefs[KEY_GHOST_VIEW_ONCE] = next.ghostViewOnceInline
            prefs[KEY_AUTO_WIPE] = next.autoWipe
            prefs[KEY_ANTI_DELETE] = next.antiDeleteRelay
        }
    }

    companion object {
        private val KEY_GHOST_READ = booleanPreferencesKey("ghost_read")
        private val KEY_GHOST_PRESENCE = booleanPreferencesKey("ghost_presence")
        private val KEY_GHOST_TYPING = booleanPreferencesKey("ghost_typing")
        private val KEY_GHOST_VIEW_ONCE = booleanPreferencesKey("ghost_view_once_inline")
        private val KEY_AUTO_WIPE = booleanPreferencesKey("auto_wipe")
        private val KEY_ANTI_DELETE = booleanPreferencesKey("anti_delete_relay")
    }
}
