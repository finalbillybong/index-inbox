package com.indexinbox.android

import android.Manifest
import android.app.Application
import android.content.Intent
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.net.Uri
import android.os.Bundle
import android.os.Build
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Archive
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.DarkMode
import androidx.compose.material.icons.filled.LightMode
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material.icons.filled.CloudQueue
import androidx.compose.material.icons.filled.FilterList
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.outlined.Star
import androidx.compose.material3.Button
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.Switch
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.core.content.FileProvider
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.jsonObject
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.ResponseBody
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.security.MessageDigest
import java.util.UUID
import javax.net.ssl.SSLException
import retrofit2.HttpException
import java.time.OffsetDateTime
import java.time.format.DateTimeFormatter

data class AppState(
    val authenticated: Boolean = false,
    val loading: Boolean = false,
    val selected: Entry? = null,
    val captureOpen: Boolean = false,
    val error: String? = null,
    val darkMode: Boolean = false,
    val themeMode: String = "system",
    val screen: String = "inbox",
    val inboxFilter: String = "active",
    val categoryFilter: String = "",
    val groupFilter: String = "",
    val notificationsEnabled: Boolean = true,
    val instantNotifications: Boolean = true,
    val widgetCaptureMode: String = "instant",
    val widgetCaptureCategory: String = "note",
    val syncStatus: String = "Saved notes available offline",
)

class IndexViewModel(
    application: Application,
    private val auth: AuthStore,
    private val dao: EntryDao,
    private val pendingDao: PendingCaptureDao,
) : AndroidViewModel(application) {
    val entries = dao.observeInbox().catch { emit(emptyList()) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())
    val pendingCaptures=pendingDao.observeAll().stateIn(viewModelScope,SharingStarted.WhileSubscribed(5_000),emptyList())
    private val _state = MutableStateFlow(AppState(authenticated=auth.token!=null,darkMode=auth.darkMode,themeMode=auth.themeMode,notificationsEnabled=auth.notificationsEnabled,instantNotifications=auth.instantNotifications,widgetCaptureMode=auth.widgetCaptureMode,widgetCaptureCategory=auth.widgetCaptureCategory))
    val state: StateFlow<AppState> = _state
    private val _groups = MutableStateFlow<List<NoteGroup>>(emptyList())
    val groups: StateFlow<List<NoteGroup>> = _groups
    private val _activity = MutableStateFlow<List<ActivityItem>>(emptyList())
    val activity: StateFlow<List<ActivityItem>> = _activity
    private val _timeline = MutableStateFlow<GroupTimeline?>(null)
    val timeline: StateFlow<GroupTimeline?> = _timeline
    private val _suggestions = MutableStateFlow<List<GroupSuggestion>>(emptyList())
    val suggestions: StateFlow<List<GroupSuggestion>> = _suggestions
    private val _serverStatus = MutableStateFlow<ServerStatus?>(null)
    val serverStatus: StateFlow<ServerStatus?> = _serverStatus
    private val _aliases = MutableStateFlow<GroupAliases?>(null)
    val aliases: StateFlow<GroupAliases?> = _aliases
    private val _devices = MutableStateFlow<List<DeviceSession>>(emptyList())
    val devices: StateFlow<List<DeviceSession>> = _devices
    private val _appUpdate = MutableStateFlow<AndroidUpdate?>(null)
    val appUpdate: StateFlow<AndroidUpdate?> = _appUpdate
    private val _updateDownloadProgress = MutableStateFlow<Int?>(null)
    val updateDownloadProgress: StateFlow<Int?> = _updateDownloadProgress
    private val _indexRingIntegration = MutableStateFlow<IndexRingIntegration?>(null)
    val indexRingIntegration: StateFlow<IndexRingIntegration?> = _indexRingIntegration
    private val _indexRingSecret = MutableStateFlow<String?>(null)
    val indexRingSecret: StateFlow<String?> = _indexRingSecret

    init {
        if (auth.token != null) {
            SyncWorker.schedule(getApplication())
            PendingCaptureWorker.schedule(getApplication())
            if(auth.notificationsEnabled&&auth.instantNotifications)InstantSyncService.start(getApplication())
            refresh()
        }
    }

    fun login(server: String, username: String, password: String) {
        if (!server.startsWith("https://")) {
            _state.value = _state.value.copy(error = "Use the HTTPS address of your Index Inbox server")
            return
        }
        viewModelScope.launch {
            _state.value=_state.value.copy(loading=true,error=null)
            try {
                val response = ApiFactory.create(server).login(
                    DeviceLoginRequest(username, password, "${Build.MANUFACTURER} ${Build.MODEL}"),
                )
                auth.save(server, response.token)
                SyncWorker.schedule(getApplication())
                PendingCaptureWorker.schedule(getApplication())
                if(auth.notificationsEnabled&&auth.instantNotifications)InstantSyncService.start(getApplication())
                _state.value=AppState(authenticated=true,darkMode=auth.darkMode,themeMode=auth.themeMode,notificationsEnabled=auth.notificationsEnabled,instantNotifications=auth.instantNotifications,widgetCaptureMode=auth.widgetCaptureMode,widgetCaptureCategory=auth.widgetCaptureCategory)
                CaptureWidgetProvider.updateAll(getApplication())
                refresh()
            } catch(error:Exception) {
                _state.value=_state.value.copy(error=friendlyLoginError(error))
            } finally {
                _state.value=_state.value.copy(loading=false)
            }
        }
    }

    private fun friendlyLoginError(error: Exception): String = when(error) {
        is UnknownHostException -> "Server not found. Check the hostname in the URL."
        is ConnectException -> "Could not connect to the server. Check the URL and that Index Inbox is running."
        is SocketTimeoutException -> "The server did not respond in time."
        is SSLException -> "Secure connection failed. Check the HTTPS certificate and hostname."
        is HttpException -> {
            val serverMessage=runCatching {
                val raw=error.response()?.errorBody()?.string().orEmpty()
                Json.parseToJsonElement(raw).jsonObject["error"]?.toString()?.trim('"')
            }.getOrNull()
            serverMessage ?: when(error.code()) {
                401 -> "Incorrect username or password."
                404 -> "Native login is unavailable. Update the Index Inbox server and check the URL."
                429 -> "Too many login attempts. Wait 15 minutes and try again."
                in 500..599 -> "The Index Inbox server returned an error (${error.code()})."
                else -> "Login failed with HTTP ${error.code()}."
            }
        }
        is IllegalArgumentException -> "The server URL is not valid. Include https:// and the hostname."
        else -> error.message?.takeIf{it.isNotBlank()} ?: "Login failed for an unknown reason."
    }

    fun refresh(query: String? = null) {
        val server = auth.serverUrl ?: return
        val token = auth.token ?: return
        viewModelScope.launch {
            _state.value=_state.value.copy(loading=true,error=null,syncStatus="Syncing…")
            try {
                val api=ApiFactory.create(server,token)
                dao.replaceAll(fetchAllEntries(api))
                _groups.value=api.groups()
                _state.value=_state.value.copy(syncStatus="Synced just now")
            } catch(error:Exception) {
                _state.value=_state.value.copy(error=error.message?:"Sync failed",syncStatus="Offline — showing saved notes")
            } finally {
                _state.value=_state.value.copy(loading=false)
            }
        }
    }

    fun select(entry: Entry?) { _state.value = _state.value.copy(selected = entry) }
    fun showCapture(open: Boolean) { _state.value = _state.value.copy(captureOpen = open) }
    fun clearError() { _state.value = _state.value.copy(error = null) }
    fun toggleTheme() {
        val enabled = !_state.value.darkMode
        auth.setDarkMode(enabled)
        auth.setThemeMode(if(enabled)"dark" else "light")
        _state.value = _state.value.copy(darkMode = enabled,themeMode=if(enabled)"dark" else "light")
        CaptureWidgetProvider.updateAll(getApplication())
    }
    fun setThemeMode(mode:String) {
        if(mode !in setOf("system","light","dark")) return
        auth.setThemeMode(mode)
        _state.value=_state.value.copy(themeMode=mode,darkMode=mode=="dark")
        CaptureWidgetProvider.updateAll(getApplication())
    }
    fun setNotifications(enabled: Boolean) {
        auth.setNotificationsEnabled(enabled)
        _state.value=_state.value.copy(notificationsEnabled=enabled)
        if(enabled&&_state.value.instantNotifications)InstantSyncService.start(getApplication()) else InstantSyncService.stop(getApplication())
    }
    fun setInstantNotifications(enabled: Boolean) {
        auth.setInstantNotifications(enabled)
        _state.value=_state.value.copy(instantNotifications=enabled)
        if(enabled&&_state.value.notificationsEnabled)InstantSyncService.start(getApplication()) else InstantSyncService.stop(getApplication())
    }
    fun setWidgetCaptureMode(mode: String) {
        if (mode !in setOf("instant", "review")) return
        auth.setWidgetCaptureMode(mode)
        _state.value = _state.value.copy(widgetCaptureMode = mode)
        CaptureWidgetProvider.updateAll(getApplication())
    }
    fun setWidgetCaptureCategory(category: String) {
        if (category !in setOf("note", "task", "idea", "question")) return
        auth.setWidgetCaptureCategory(category)
        _state.value = _state.value.copy(widgetCaptureCategory = category)
        CaptureWidgetProvider.updateAll(getApplication())
    }
    fun setFilter(filter: String) { _state.value = _state.value.copy(inboxFilter = filter) }
    fun setCategoryFilter(category: String) { _state.value = _state.value.copy(categoryFilter = category) }
    fun setGroupFilter(group: String) { _state.value = _state.value.copy(groupFilter = group) }
    fun showScreen(screen: String) {
        _state.value = _state.value.copy(screen = screen)
        if (screen == "groups") loadGroups()
        if (screen == "activity") loadActivity()
        if (screen == "suggestions") loadSuggestions()
        if (screen == "status") loadStatus()
    }
    fun openNotification(target:String?,entryId:String?) {
        if(target=="entry"&&!entryId.isNullOrBlank()) viewModelScope.launch {
            dao.get(entryId)?.let { _state.value=_state.value.copy(screen="inbox",selected=it) }
                ?: run { refresh(); _state.value=_state.value.copy(screen="inbox") }
        } else if(target=="activity") showScreen("activity")
        else _state.value=_state.value.copy(screen="inbox")
    }

    private fun loadGroups() {
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy { _groups.value=ApiFactory.create(server,token).groups() } }
    }

    private fun loadActivity() {
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy { _activity.value=ApiFactory.create(server,token).activity() } }
    }

    fun openGroup(name: String) {
        val server=auth.serverUrl?:return; val token=auth.token?:return
        _state.value=_state.value.copy(screen="timeline")
        viewModelScope.launch { busy { _timeline.value=ApiFactory.create(server,token).groupTimeline(name) } }
    }

    fun updateTimelineEntry(groupName: String,entry: Entry,update: EntryUpdate) {
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy {
            val api=ApiFactory.create(server,token)
            api.update(entry.id,update)
            _timeline.value=api.groupTimeline(groupName)
            dao.replaceAll(fetchAllEntries(api))
        } }
    }

    fun toggleGroup(group: NoteGroup) {
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy {
            ApiFactory.create(server,token).updateGroup(group.name,GroupUpdate(archived=group.archived==0))
            _groups.value=ApiFactory.create(server,token).groups()
        } }
    }

    fun renameGroup(group: NoteGroup,newName: String) {
        if(newName.isBlank()||newName==group.name)return
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy {
            ApiFactory.create(server,token).updateGroup(group.name,GroupUpdate(name=newName.trim()))
            _groups.value=ApiFactory.create(server,token).groups()
        } }
    }

    fun removeGroup(group: NoteGroup) {
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy {
            val api=ApiFactory.create(server,token)
            api.deleteGroup(group.name)
            _groups.value=api.groups()
            refresh()
        } }
    }

    fun openAliases(name: String) {
        val server=auth.serverUrl?:return; val token=auth.token?:return
        _state.value=_state.value.copy(screen="aliases")
        viewModelScope.launch { busy { _aliases.value=ApiFactory.create(server,token).groupAliases(name) } }
    }

    fun addAlias(alias: String) {
        val current=_aliases.value?:return
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy {
            val api=ApiFactory.create(server,token)
            api.addAlias(current.group,AliasRequest(alias))
            _aliases.value=api.groupAliases(current.group)
        } }
    }

    fun removeAlias(alias: String) {
        val current=_aliases.value?:return
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy {
            val api=ApiFactory.create(server,token)
            api.removeAlias(current.group,AliasRequest(alias))
            _aliases.value=api.groupAliases(current.group)
        } }
    }

    private fun loadSuggestions() {
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy { _suggestions.value=ApiFactory.create(server,token).suggestions() } }
    }

    fun resolveSuggestion(suggestion: GroupSuggestion, action: String) {
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy {
            ApiFactory.create(server,token).resolveSuggestion(suggestion.entryId,action,SuggestionRequest(suggestion.group))
            _suggestions.value=ApiFactory.create(server,token).suggestions()
            refresh()
        } }
    }

    fun bulk(ids: Set<String>, action: String) {
        if(ids.isEmpty())return
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy {
            val api=ApiFactory.create(server,token)
            if(action=="delete")api.deleteBulk(BulkRequest(ids.toList(),action))
            else api.bulk(BulkRequest(ids.toList(),action))
            refresh()
        } }
    }

    private fun loadStatus() {
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy {
            val api=ApiFactory.create(server,token)
            _serverStatus.value=api.status()
            _devices.value=api.devices()
            _appUpdate.value=runCatching{api.androidUpdate()}.getOrNull()
            _indexRingIntegration.value=runCatching{api.indexRingIntegration()}.getOrNull()
        } }
    }

    fun revealIndexRingSecret(password:String) {
        val server=auth.serverUrl?:return;val token=auth.token?:return
        viewModelScope.launch { busy {
            _indexRingSecret.value=ApiFactory.create(server,token)
                .revealIndexRingSecret(IntegrationPasswordRequest(password)).secret
        } }
    }

    fun rotateIndexRingSecret(password:String) {
        val server=auth.serverUrl?:return;val token=auth.token?:return
        viewModelScope.launch { busy {
            val api=ApiFactory.create(server,token)
            _indexRingSecret.value=api.rotateIndexRingSecret(IntegrationPasswordRequest(password)).secret
            _indexRingIntegration.value=api.indexRingIntegration()
            _state.value=_state.value.copy(error="Webhook secret rotated. Update the Pebble app now.")
        } }
    }

    fun testIndexRing() {
        val secret=_indexRingSecret.value
        if(secret.isNullOrBlank()){_state.value=_state.value.copy(error="Reveal the webhook secret before testing.");return}
        val server=auth.serverUrl?:return;val token=auth.token?:return
        viewModelScope.launch { busy {
            ApiFactory.create(server,token).testIndexRing(secret,ManualCapture("Index Ring connection test"))
            _state.value=_state.value.copy(error="Test capture added to the inbox.")
            refresh()
        } }
    }

    fun checkForUpdate() {
        val server=auth.serverUrl?:return;val token=auth.token?:return
        viewModelScope.launch { busy {
            val update=ApiFactory.create(server,token).androidUpdate()
            _appUpdate.value=update
            _state.value=_state.value.copy(error=when {
                !update.available -> "No self-hosted app release is configured."
                update.versionCode<=BuildConfig.VERSION_CODE -> "Index Inbox is up to date."
                else -> "Index Inbox ${update.versionName} is available."
            })
        } }
    }

    fun installUpdate() {
        val update=_appUpdate.value?.takeIf{it.available&&it.versionCode>BuildConfig.VERSION_CODE}?:return
        val server=auth.serverUrl?:return;val token=auth.token?:return
        viewModelScope.launch {
            _state.value = _state.value.copy(loading = true, error = null)
            _updateDownloadProgress.value = 0
            try {
            val context=getApplication<Application>()
            val directory=File(context.getExternalFilesDir(null),"updates").apply{mkdirs()}
            val target=File(directory,"index-inbox-${update.versionCode}.apk")
            withContext(Dispatchers.IO) {
                val body=ApiFactory.create(server,token).androidUpdateApk()
                val total=update.bytes.takeIf{it>0} ?: body.contentLength().takeIf{it>0} ?: 0L
                body.byteStream().use { input ->
                    target.outputStream().use { output ->
                        val buffer=ByteArray(64*1024)
                        var downloaded=0L
                        while(true) {
                            val count=input.read(buffer)
                            if(count<0)break
                            output.write(buffer,0,count)
                            downloaded+=count
                            if(total>0) _updateDownloadProgress.value=downloadProgress(downloaded,total)
                        }
                    }
                }
            }
            val digest=withContext(Dispatchers.IO) {
                val hash=MessageDigest.getInstance("SHA-256")
                target.inputStream().use { input ->
                    val buffer=ByteArray(1024*1024)
                    while(true) {
                        val count=input.read(buffer)
                        if(count<0)break
                        hash.update(buffer,0,count)
                    }
                }
                hash.digest().joinToString(""){"%02x".format(it)}
            }
            if(!digest.equals(update.sha256,ignoreCase=true)){target.delete();throw IOException("Downloaded update failed its checksum")}
            _updateDownloadProgress.value=100
            val uri=FileProvider.getUriForFile(context,"${context.packageName}.files",target)
            context.startActivity(Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri,"application/vnd.android.package-archive")
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_GRANT_READ_URI_PERMISSION)
            })
            } catch(error:Exception) {
                _state.value=_state.value.copy(error=error.message?:"Update download failed")
            } finally {
                _state.value=_state.value.copy(loading=false)
                _updateDownloadProgress.value=null
            }
        }
    }

    fun revokeOtherDevices() {
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy {
            val api=ApiFactory.create(server,token)
            val result=api.revokeOtherDevices()
            _devices.value=api.devices()
            _state.value=_state.value.copy(error="Revoked ${result.revoked} other device sessions")
        } }
    }

    fun createBackup() {
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy {
            val result=ApiFactory.create(server,token).createBackup()
            _serverStatus.value=ApiFactory.create(server,token).status()
            _state.value=_state.value.copy(error="Created ${result.backup.archiveName ?: "verified backup"}")
        } }
    }

    fun runRetention(days: Int) {
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy {
            val result=ApiFactory.create(server,token).retention(RetentionRequest(days))
            _serverStatus.value=ApiFactory.create(server,token).status()
            _state.value=_state.value.copy(error="Removed ${result.removed} audio files")
            refresh()
        } }
    }

    fun downloadExport(format: String,uri: Uri) = download(uri,"Export saved") { it.export(format) }
    fun downloadGroupExport(name: String,format: String,uri: Uri) = download(uri,"Group export saved") { it.groupExport(name,format) }
    fun downloadLatestBackup(uri: Uri) = download(uri,"Verified backup saved") { it.latestBackup() }
    fun downloadAudio(entry: Entry,uri: Uri) = download(uri,"Audio saved") { it.audioDownload(entry.id) }

    fun triggerBackupHook() {
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy {
            ApiFactory.create(server,token).triggerBackupHook()
            _state.value=_state.value.copy(error="External backup hook triggered")
        } }
    }

    private fun download(uri: Uri,successMessage: String,load: suspend (IndexApi)->ResponseBody) {
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy {
            val body=load(ApiFactory.create(server,token))
            withContext(Dispatchers.IO) {
                getApplication<Application>().contentResolver.openOutputStream(uri,"w")?.use { output ->
                    body.byteStream().use { input -> input.copyTo(output,64*1024) }
                } ?: error("The selected file could not be opened")
            }
            _state.value=_state.value.copy(error=successMessage)
        } }
    }
    fun audioSource(entry: Entry): Pair<String, String>? {
        val server = auth.serverUrl ?: return null
        val token = auth.token ?: return null
        return "${server}api/entries/${entry.id}/audio" to token
    }

    fun update(entry: Entry, update: EntryUpdate) {
        val server = auth.serverUrl ?: return
        val token = auth.token ?: return
        viewModelScope.launch {
            busy {
                ApiFactory.create(server, token).update(entry.id, update)
                select(null)
                refresh()
            }
        }
    }

    fun assignGroup(entry: Entry, group: String?) {
        val server=auth.serverUrl?:return; val token=auth.token?:return
        viewModelScope.launch { busy {
            val body=buildJsonObject { put("group_name",if(group==null)JsonNull else JsonPrimitive(group)) }
            ApiFactory.create(server,token).assignGroup(entry.id,body)
            select(null)
            refresh()
        } }
    }

    fun delete(entry: Entry) {
        val server = auth.serverUrl ?: return
        val token = auth.token ?: return
        viewModelScope.launch {
            busy {
                try {
                    ApiFactory.create(server, token).delete(entry.id)
                } catch (error: HttpException) {
                    if(error.code()!=404)throw error
                }
                dao.delete(entry.id)
                select(null)
            }
        }
    }

    fun capture(text: String, title: String = "", category: String = "note") {
        if (text.isBlank()) return
        val server = auth.serverUrl ?: return
        val token = auth.token ?: return
        val captureId=UUID.randomUUID().toString()
        viewModelScope.launch {
            _state.value=_state.value.copy(loading=true,error=null)
            try {
                ApiFactory.create(server, token).capture(ManualCapture(text.trim(), title.trim(),category,id=captureId))
                showCapture(false)
                refresh()
            } catch(error:Exception) {
                if(shouldQueue(error)) {
                    queueCapture(title,text,category,null,error,captureId)
                    showCapture(false)
                } else _state.value=_state.value.copy(error=error.message?:"Capture failed")
            } finally {
                _state.value=_state.value.copy(loading=false)
            }
        }
    }

    fun captureAudio(file: File, text: String, title: String = "", category: String = "note") {
        val server = auth.serverUrl ?: return
        val token = auth.token ?: return
        val captureId=UUID.randomUUID().toString()
        viewModelScope.launch {
            _state.value=_state.value.copy(loading=true,error=null)
            try {
                val mediaType = "audio/mp4".toMediaType()
                val part = MultipartBody.Part.createFormData("audio", "recording.m4a", file.asRequestBody(mediaType))
                val plain = "text/plain".toMediaType()
                ApiFactory.create(server, token).captureAudio(
                    part,
                    text.trim().toRequestBody(plain),
                    title.trim().toRequestBody(plain),
                    category.toRequestBody(plain),
                    System.currentTimeMillis().toString().toRequestBody(plain),
                    captureId.toRequestBody(plain),
                )
                file.delete()
                showCapture(false)
                refresh()
            } catch(error:Exception) {
                if(shouldQueue(error)) {
                    val directory=File(getApplication<Application>().filesDir,"pending-audio").apply{mkdirs()}
                    val durable=File(directory,"${UUID.randomUUID()}.m4a")
                    file.copyTo(durable,overwrite=true)
                    queueCapture(title,text,category,durable.absolutePath,error,captureId)
                    showCapture(false)
                } else _state.value=_state.value.copy(error=error.message?:"Capture failed")
            } finally {
                _state.value=_state.value.copy(loading=false)
            }
        }
    }

    private fun shouldQueue(error: Exception)=error is IOException||(error is HttpException&&error.code()>=500)

    private suspend fun queueCapture(title:String,text:String,category:String,audioPath:String?,error:Exception,captureId:String=UUID.randomUUID().toString()) {
        pendingDao.upsert(PendingCapture(captureId,title.trim(),text.trim(),category,audioPath,System.currentTimeMillis(),error.message?:"Offline"))
        PendingCaptureWorker.schedule(getApplication())
        _state.value=_state.value.copy(error="Saved to pending captures; it will retry automatically")
    }

    fun updatePending(capture: PendingCapture,title:String,text:String,category:String) {
        viewModelScope.launch { pendingDao.upsert(capture.copy(title=title,transcription=text,category=category,lastError="")) }
    }

    fun discardPending(capture: PendingCapture) {
        viewModelScope.launch {
            capture.audioPath?.let{File(it).delete()}
            pendingDao.delete(capture.id)
        }
    }

    fun retryPending() { PendingCaptureWorker.schedule(getApplication()) }

    fun transcribeAudio(file: File, onResult: (Result<String>) -> Unit) {
        val server = auth.serverUrl ?: return
        val token = auth.token ?: return
        viewModelScope.launch {
            _state.value = _state.value.copy(loading = true, error = null)
            try {
                val part = MultipartBody.Part.createFormData(
                    "audio",
                    "recording.m4a",
                    file.asRequestBody("audio/mp4".toMediaType()),
                )
                onResult(Result.success(ApiFactory.create(server, token).transcribe(part).transcription))
            } catch (error: Exception) {
                onResult(Result.failure(error))
            } finally {
                _state.value = _state.value.copy(loading = false)
            }
        }
    }

    fun logout() {
        val server = auth.serverUrl
        val token = auth.token
        viewModelScope.launch {
            if (server != null && token != null) runCatching { ApiFactory.create(server, token).logout() }
            auth.clear()
            SyncWorker.cancel(getApplication())
            InstantSyncService.stop(getApplication())
            CaptureWidgetProvider.updateAll(getApplication())
            _state.value=AppState(darkMode=auth.darkMode,themeMode=auth.themeMode,notificationsEnabled=auth.notificationsEnabled,instantNotifications=auth.instantNotifications,widgetCaptureMode=auth.widgetCaptureMode,widgetCaptureCategory=auth.widgetCaptureCategory)
        }
    }

    private suspend fun busy(block: suspend () -> Unit) {
        _state.value = _state.value.copy(loading = true, error = null)
        try { block() }
        catch (error: Exception) {
            _state.value = _state.value.copy(error = error.message ?: "Request failed")
        } finally {
            _state.value = _state.value.copy(loading = false)
        }
    }
}

class MainActivity : ComponentActivity() {
    private lateinit var viewModel: IndexViewModel

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val auth = AuthStore(this)
        val database=IndexDatabase.get(this)
        val dao=database.entries()
        viewModel = ViewModelProvider(this, object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T = IndexViewModel(application,auth,dao,database.pending()) as T
        })[IndexViewModel::class.java]
        receiveSharedText(intent)
        receiveNotification(intent)
        receiveWidgetAction(intent)
        setContent {
            val state by viewModel.state.collectAsState()
            val systemDark=isSystemInDarkTheme()
            val effectiveDark=when(state.themeMode){"dark"->true;"light"->false;else->systemDark}
            MaterialTheme(colorScheme = indexColorScheme(effectiveDark)) {
                IndexApp(viewModel,effectiveDark)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        receiveSharedText(intent)
        receiveNotification(intent)
        receiveWidgetAction(intent)
    }

    private fun receiveSharedText(intent: Intent?) {
        if (intent?.action == Intent.ACTION_SEND && intent.type == "text/plain") {
            intent.getStringExtra(Intent.EXTRA_TEXT)?.takeIf { it.isNotBlank() }?.let {
                viewModel.showCapture(true)
                intent.removeExtra(Intent.EXTRA_TEXT)
                SharedCapture.text = it
            }
        }
    }
    private fun receiveNotification(intent:Intent?) {
        val target=intent?.getStringExtra("notification_target") ?: return
        viewModel.openNotification(target,intent.getStringExtra("entry_id"))
        intent.removeExtra("notification_target")
        intent.removeExtra("entry_id")
    }

    private fun receiveWidgetAction(intent: Intent?) {
        when (intent?.action) {
            ACTION_WIDGET_SETUP -> {
                if (viewModel.state.value.authenticated) {
                    SharedCapture.category = AuthStore(this).widgetCaptureCategory
                    SharedCapture.audioPath = CaptureWidgetState.file(this)?.let(::File)?.takeIf(File::exists)?.absolutePath
                    SharedCapture.status = if (SharedCapture.audioPath == null) {
                        "Grant microphone access, then record once to finish widget setup."
                    } else {
                        "A recording was not sent. Review it and try saving again."
                    }
                    viewModel.showCapture(true)
                }
                intent.action = null
            }
            ACTION_WIDGET_REVIEW -> {
                AudioCaptureService.finishForReview(this)?.let {
                    SharedCapture.audioPath = it.absolutePath
                    SharedCapture.category = AuthStore(this).widgetCaptureCategory
                    SharedCapture.status = "Transcribing on your server…"
                    viewModel.showCapture(true)
                }
                intent.action = null
            }
        }
    }

    companion object {
        const val ACTION_WIDGET_SETUP = "com.indexinbox.android.widget.SETUP"
        const val ACTION_WIDGET_REVIEW = "com.indexinbox.android.widget.REVIEW"
    }
}

private object SharedCapture {
    var text: String = ""
    var audioPath: String? = null
    var status: String = ""
    var category: String = "note"
}

internal fun filterInboxEntries(
    entries:List<Entry>,
    query:String,
    filter:String,
    categoryFilter:String,
    groupFilter:String,
)=entries.filter {
    when(filter) {
        "all" -> true
        "starred" -> it.starred==1&&it.archived==0
        "unprocessed" -> it.processed==0&&it.archived==0
        "archived" -> it.archived==1
        else -> it.archived==0
    }
}.filter{categoryFilter.isBlank()||it.category==categoryFilter}
 .filter{groupFilter.isBlank()||it.groupName==groupFilter}
 .filter{query.isBlank()||it.title.contains(query,true)||it.transcription.contains(query,true)||it.tags.contains(query,true)}

private fun indexColorScheme(dark: Boolean) = if (dark) {
    androidx.compose.material3.darkColorScheme(
        primary = androidx.compose.ui.graphics.Color(0xFFFFCA48),
        secondary = androidx.compose.ui.graphics.Color(0xFFD8BD79),
        background = androidx.compose.ui.graphics.Color(0xFF0C0D10),
        surface = androidx.compose.ui.graphics.Color(0xFF17191E),
    )
} else {
    androidx.compose.material3.lightColorScheme(
        primary = androidx.compose.ui.graphics.Color(0xFF315C49),
        secondary = androidx.compose.ui.graphics.Color(0xFF78633D),
        background = androidx.compose.ui.graphics.Color(0xFFF7F5EF),
        surface = androidx.compose.ui.graphics.Color(0xFFFFFCF5),
    )
}

@Composable
fun IndexApp(viewModel: IndexViewModel,effectiveDark:Boolean) {
    val state by viewModel.state.collectAsState()
    val entries by viewModel.entries.collectAsState()
    val groups by viewModel.groups.collectAsState()
    val activity by viewModel.activity.collectAsState()
    val timeline by viewModel.timeline.collectAsState()
    val suggestions by viewModel.suggestions.collectAsState()
    val serverStatus by viewModel.serverStatus.collectAsState()
    val aliases by viewModel.aliases.collectAsState()
    val devices by viewModel.devices.collectAsState()
    val appUpdate by viewModel.appUpdate.collectAsState()
    val updateDownloadProgress by viewModel.updateDownloadProgress.collectAsState()
    val indexRingIntegration by viewModel.indexRingIntegration.collectAsState()
    val indexRingSecret by viewModel.indexRingSecret.collectAsState()
    val pending by viewModel.pendingCaptures.collectAsState()
    val snackbar = remember { SnackbarHostState() }
    val notificationPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}
    LaunchedEffect(state.authenticated) {
        if (state.authenticated && Build.VERSION.SDK_INT >= 33) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }
    LaunchedEffect(state.error) {
        state.error?.let { snackbar.showSnackbar(it); viewModel.clearError() }
    }
    when {
        !state.authenticated -> LoginScreen(state.loading,state.error,effectiveDark,viewModel::setThemeMode,viewModel::login)
        state.screen == "groups" -> GroupsScreen(
            groups, state.loading,
            onBack = { viewModel.showScreen("inbox") },
            onOpen = viewModel::openGroup,
            onToggle = viewModel::toggleGroup,
            onRename = viewModel::renameGroup,
            onAliases = viewModel::openAliases,
            onRemove = viewModel::removeGroup,
            onSuggestions = { viewModel.showScreen("suggestions") },
        )
        state.screen == "timeline" -> GroupTimelineScreen(
            timeline,state.loading,
            onBack={viewModel.showScreen("groups")},
            onSave={entry,update -> timeline?.group?.name?.let{viewModel.updateTimelineEntry(it,entry,update)}},
            audioSource=viewModel::audioSource,
            onExport=viewModel::downloadGroupExport,
        )
        state.screen == "aliases" -> AliasesScreen(
            aliases,state.loading,
            onBack={viewModel.showScreen("groups")},
            onAdd=viewModel::addAlias,
            onRemove=viewModel::removeAlias,
        )
        state.screen == "suggestions" -> SuggestionsScreen(
            suggestions, state.loading,
            onBack = { viewModel.showScreen("groups") },
            onResolve = viewModel::resolveSuggestion,
        )
        state.screen == "status" -> StatusScreen(
            serverStatus, state.loading,
            devices=devices,
            appUpdate=appUpdate,
            updateDownloadProgress=updateDownloadProgress,
            indexRingIntegration=indexRingIntegration,
            indexRingSecret=indexRingSecret,
            notificationsEnabled=state.notificationsEnabled,
            instantNotifications=state.instantNotifications,
            widgetCaptureMode=state.widgetCaptureMode,
            widgetCaptureCategory=state.widgetCaptureCategory,
            onBack={viewModel.showScreen("inbox")},
            onBackup=viewModel::createBackup,
            onRetention=viewModel::runRetention,
            onExport=viewModel::downloadExport,
            onDownloadBackup=viewModel::downloadLatestBackup,
            onBackupHook=viewModel::triggerBackupHook,
            onNotifications=viewModel::setNotifications,
            onInstantNotifications=viewModel::setInstantNotifications,
            onWidgetCaptureMode=viewModel::setWidgetCaptureMode,
            onWidgetCaptureCategory=viewModel::setWidgetCaptureCategory,
            onRevokeOthers=viewModel::revokeOtherDevices,
            onCheckUpdate=viewModel::checkForUpdate,
            onInstallUpdate=viewModel::installUpdate,
            onRevealIndexRing=viewModel::revealIndexRingSecret,
            onRotateIndexRing=viewModel::rotateIndexRingSecret,
            onTestIndexRing=viewModel::testIndexRing,
        )
        state.screen == "activity" -> ActivityScreen(activity, state.loading) { viewModel.showScreen("inbox") }
        state.screen == "pending" -> PendingCapturesScreen(
            pending,state.loading,
            onBack={viewModel.showScreen("inbox")},
            onSave=viewModel::updatePending,
            onDiscard=viewModel::discardPending,
            onRetry=viewModel::retryPending,
        )
        state.captureOpen -> CaptureScreen(
            initial = SharedCapture.text,
            initialAudio = SharedCapture.audioPath?.let(::File)?.takeIf(File::exists),
            initialStatus = SharedCapture.status,
            initialCategory = SharedCapture.category,
            loading = state.loading,
            onClose = {
                val widgetAudio = SharedCapture.audioPath != null
                SharedCapture.text = ""; SharedCapture.audioPath = null; SharedCapture.status = ""; SharedCapture.category = "note"
                if (widgetAudio) CaptureWidgetState.set(viewModel.getApplication(), "ready")
                viewModel.showCapture(false)
            },
            onSave = { title, text, category -> SharedCapture.text = ""; SharedCapture.audioPath = null; SharedCapture.status = ""; SharedCapture.category = "note"; viewModel.capture(text, title,category) },
            onSaveAudio = { title, text, category, file ->
                val widgetAudio = SharedCapture.audioPath != null
                SharedCapture.text = ""; SharedCapture.audioPath = null; SharedCapture.status = ""; SharedCapture.category = "note"
                if (widgetAudio) CaptureWidgetState.set(viewModel.getApplication(), "ready")
                viewModel.captureAudio(file, text,title,category)
            },
            onTranscribe = viewModel::transcribeAudio,
        )
        state.selected != null -> EntryScreen(
            entry = state.selected!!,
            groups = groups.filter { it.archived == 0 || it.name == state.selected!!.groupName },
            loading = state.loading,
            onBack = { viewModel.select(null) },
            onSave = { viewModel.update(state.selected!!, it) },
            onDelete = { viewModel.delete(state.selected!!) },
            onAssignGroup = { viewModel.assignGroup(state.selected!!,it) },
            onDownloadAudio = { uri -> viewModel.downloadAudio(state.selected!!,uri) },
            audioSource = viewModel.audioSource(state.selected!!),
        )
        else -> InboxScreen(
            entries = entries,
            loading = state.loading,
            snackbar = snackbar,
            onRefresh = { viewModel.refresh() },
            onSearch = viewModel::refresh,
            onSelect = viewModel::select,
            onCapture = { viewModel.showCapture(true) },
            onLogout = viewModel::logout,
            darkMode = effectiveDark,
            themeMode = state.themeMode,
            onThemeMode = viewModel::setThemeMode,
            filter = state.inboxFilter,
            onFilter = viewModel::setFilter,
            categoryFilter = state.categoryFilter,
            onCategoryFilter = viewModel::setCategoryFilter,
            groupFilter = state.groupFilter,
            onGroupFilter = viewModel::setGroupFilter,
            groups = groups,
            onGroups = { viewModel.showScreen("groups") },
            onActivity = { viewModel.showScreen("activity") },
            onStatus = { viewModel.showScreen("status") },
            pendingCount=pending.size,
            syncStatus=state.syncStatus,
            onPending={viewModel.showScreen("pending")},
            onBulk = viewModel::bulk,
            onStar = { entry -> viewModel.update(entry, EntryUpdate(starred = entry.starred == 0)) },
        )
    }
}

@Composable
private fun LoginScreen(loading:Boolean,error:String?,darkMode:Boolean,onThemeMode:(String)->Unit,onLogin:(String,String,String)->Unit) {
    var server by remember { mutableStateOf("") }
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).padding(28.dp), contentAlignment = Alignment.Center) {
        Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
                Icon(
                    painter=painterResource(R.drawable.ic_launcher_foreground),
                    contentDescription=null,
                    tint=androidx.compose.ui.graphics.Color.Unspecified,
                    modifier=Modifier.size(46.dp),
                )
                Spacer(Modifier.width(10.dp))
                Text("Index Inbox",style=MaterialTheme.typography.headlineMedium,fontWeight=FontWeight.Bold,color=MaterialTheme.colorScheme.onBackground,modifier=Modifier.weight(1f))
                IconButton(onClick={onThemeMode(if(darkMode)"light" else "dark")}) {
                    Icon(if(darkMode)Icons.Default.LightMode else Icons.Default.DarkMode,if(darkMode)"Use light mode" else "Use dark mode")
                }
            }
            Text("Your recordings. Your server.", color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(server, { server = it }, label = { Text("Server URL") }, placeholder = { Text("https://index.example.com") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(username, { username = it }, label = { Text("Username") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(password, { password = it }, label = { Text("Password") }, visualTransformation = PasswordVisualTransformation(), singleLine = true, modifier = Modifier.fillMaxWidth())
            if(!error.isNullOrBlank()) Text(error,color=MaterialTheme.colorScheme.error,style=MaterialTheme.typography.bodyMedium)
            Button(onClick = { onLogin(server, username, password) }, enabled = !loading && server.isNotBlank() && username.isNotBlank() && password.isNotBlank(), modifier = Modifier.fillMaxWidth()) {
                Text(if (loading) "Connecting…" else "Connect")
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun InboxScreen(
    entries: List<Entry>,
    loading: Boolean,
    snackbar: SnackbarHostState,
    onRefresh: () -> Unit,
    onSearch: (String) -> Unit,
    onSelect: (Entry) -> Unit,
    onCapture: () -> Unit,
    onLogout: () -> Unit,
    darkMode: Boolean,
    themeMode: String,
    onThemeMode: (String) -> Unit,
    filter: String,
    onFilter: (String) -> Unit,
    categoryFilter: String,
    onCategoryFilter: (String) -> Unit,
    groupFilter: String,
    onGroupFilter: (String) -> Unit,
    groups: List<NoteGroup>,
    onGroups: () -> Unit,
    onActivity: () -> Unit,
    onStatus: () -> Unit,
    pendingCount: Int,
    syncStatus: String,
    onPending: () -> Unit,
    onBulk: (Set<String>,String) -> Unit,
    onStar: (Entry) -> Unit,
) {
    var query by remember { mutableStateOf("") }
    var selected by remember { mutableStateOf(setOf<String>()) }
    var pendingDelete by remember { mutableStateOf<Set<String>?>(null) }
    var menuExpanded by remember { mutableStateOf(false) }
    var filtersExpanded by remember { mutableStateOf(false) }
    pendingDelete?.let { ids ->
        AlertDialog(
            onDismissRequest={pendingDelete=null},
            title={Text("Delete ${ids.size} entries?")},
            text={Text("This permanently removes the selected notes and their stored audio.")},
            confirmButton={TextButton(onClick={onBulk(ids,"delete");selected=emptySet();pendingDelete=null}){Text("Delete all")}},
            dismissButton={TextButton(onClick={pendingDelete=null}){Text("Cancel")}},
        )
    }
    val visibleEntries=remember(entries,query,filter,categoryFilter,groupFilter) {
        filterInboxEntries(entries,query,filter,categoryFilter,groupFilter)
    }
    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment=Alignment.CenterVertically) {
                        Icon(
                            painter=painterResource(R.drawable.ic_launcher_foreground),
                            contentDescription=null,
                            tint=androidx.compose.ui.graphics.Color.Unspecified,
                            modifier=Modifier.size(38.dp),
                        )
                        Spacer(Modifier.width(8.dp))
                        Text("Index Inbox",style=MaterialTheme.typography.titleLarge,fontWeight=FontWeight.Bold)
                    }
                },
                actions = {
                    Box {
                        IconButton(onClick={menuExpanded=true}){Icon(Icons.Default.Menu,"Menu")}
                        DropdownMenu(expanded=menuExpanded,onDismissRequest={menuExpanded=false}) {
                            DropdownMenuItem(text={Text("Groups")},leadingIcon={Icon(Icons.Default.Folder,null)},onClick={menuExpanded=false;onGroups()})
                            DropdownMenuItem(text={Text("Recent activity")},leadingIcon={Icon(Icons.Default.History,null)},onClick={menuExpanded=false;onActivity()})
                            DropdownMenuItem(text={Text("Storage, backups & settings")},leadingIcon={Icon(Icons.Default.Storage,null)},onClick={menuExpanded=false;onStatus()})
                            DropdownMenuItem(text={Text("Pending captures${if(pendingCount>0)" ($pendingCount)" else ""}")},leadingIcon={Icon(Icons.Default.CloudQueue,null)},onClick={menuExpanded=false;onPending()})
                            HorizontalDivider()
                            Text("Theme",modifier=Modifier.padding(horizontal=16.dp,vertical=8.dp),fontWeight=FontWeight.Bold)
                            listOf("system" to "Follow system","light" to "Light","dark" to "Dark").forEach { (mode,label) ->
                                DropdownMenuItem(
                                    text={Text(label)},
                                    leadingIcon={if(themeMode==mode)Icon(Icons.Default.Check,null)},
                                    onClick={menuExpanded=false;onThemeMode(mode)},
                                )
                            }
                            DropdownMenuItem(text={Text("Refresh")},leadingIcon={Icon(Icons.Default.Refresh,null)},enabled=!loading,onClick={menuExpanded=false;onRefresh()})
                            DropdownMenuItem(text={Text("Sign out")},leadingIcon={Icon(Icons.Default.Close,null)},onClick={menuExpanded=false;onLogout()})
                        }
                    }
                },
            )
        },
        floatingActionButton = { FloatingActionButton(onClick = onCapture) { Icon(Icons.Default.Add, "Capture") } },
    ) { padding ->
        Column(Modifier.padding(padding)) {
            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                leadingIcon = { Icon(Icons.Default.Search, null) },
                placeholder = { Text("Search your inbox") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = { onSearch(query) }),
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
            )
            Row(
                Modifier.fillMaxWidth().padding(horizontal=18.dp,vertical=2.dp),
                verticalAlignment=Alignment.CenterVertically,
                horizontalArrangement=Arrangement.spacedBy(6.dp),
            ) {
                Icon(Icons.Default.CloudQueue,null,modifier=Modifier.size(16.dp),tint=MaterialTheme.colorScheme.onSurfaceVariant)
                Text(
                    if(pendingCount>0)"$syncStatus · $pendingCount pending" else syncStatus,
                    style=MaterialTheme.typography.labelSmall,
                    color=MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier=Modifier.weight(1f),
                )
                if(pendingCount>0) TextButton(onClick=onPending){Text("Review")}
            }
            Box(Modifier.padding(horizontal=16.dp,vertical=4.dp)) {
                val stateLabel = mapOf("active" to "Active","all" to "All","unprocessed" to "Unprocessed","starred" to "Starred","archived" to "Archived")[filter] ?: "Active"
                val typeLabel = mapOf("" to "All types","note" to "Notes","task" to "Tasks","idea" to "Ideas","question" to "Questions")[categoryFilter] ?: "All types"
                OutlinedButton(onClick={filtersExpanded=true}) {
                    Icon(Icons.Default.FilterList,null)
                    Spacer(Modifier.width(8.dp))
                    Text(listOf(stateLabel,typeLabel).plus(if(groupFilter.isNotBlank()) listOf(groupFilter) else emptyList()).joinToString(" · "))
                }
                DropdownMenu(expanded=filtersExpanded,onDismissRequest={filtersExpanded=false}) {
                    Text("State",modifier=Modifier.padding(horizontal=16.dp,vertical=8.dp),fontWeight=FontWeight.Bold)
                    listOf("active" to "Active","all" to "All","unprocessed" to "Unprocessed","starred" to "Starred","archived" to "Archived").forEach { (value,label) ->
                        DropdownMenuItem(
                            text={Text(label)},
                            leadingIcon={if(filter==value) Icon(Icons.Default.Check,null)},
                            onClick={filtersExpanded=false;onFilter(value)},
                        )
                    }
                    HorizontalDivider()
                    Text("Type",modifier=Modifier.padding(horizontal=16.dp,vertical=8.dp),fontWeight=FontWeight.Bold)
                    listOf("" to "All types","note" to "Notes","task" to "Tasks","idea" to "Ideas","question" to "Questions").forEach { (value,label) ->
                        DropdownMenuItem(
                            text={Text(label)},
                            leadingIcon={if(categoryFilter==value) Icon(Icons.Default.Check,null)},
                            onClick={filtersExpanded=false;onCategoryFilter(value)},
                        )
                    }
                    if(groups.isNotEmpty()) {
                        HorizontalDivider()
                        Text("Group",modifier=Modifier.padding(horizontal=16.dp,vertical=8.dp),fontWeight=FontWeight.Bold)
                        DropdownMenuItem(
                            text={Text("All groups")},
                            leadingIcon={if(groupFilter.isBlank()) Icon(Icons.Default.Check,null)},
                            onClick={filtersExpanded=false;onGroupFilter("")},
                        )
                        groups.forEach { group ->
                            DropdownMenuItem(
                                text={Text(group.name)},
                                leadingIcon={if(groupFilter==group.name) Icon(Icons.Default.Check,null)},
                                onClick={filtersExpanded=false;onGroupFilter(group.name)},
                            )
                        }
                    }
                }
            }
            if(selected.isNotEmpty()) {
                Row(
                    Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal=16.dp,vertical=6.dp),
                    horizontalArrangement=Arrangement.spacedBy(8.dp),
                ) {
                    Text("${selected.size} selected",modifier=Modifier.align(Alignment.CenterVertically),fontWeight=FontWeight.Bold)
                    listOf(
                        (if(filter=="archived")"restore" to "Restore" else "archive" to "Archive"),
                        (if(filter=="unprocessed")"process" to "Process" else "unprocess" to "Unprocess"),
                        (if(filter=="starred")"unstar" to "Unstar" else "star" to "Star"),
                        "delete" to "Delete",
                    ).forEach { (action,label) ->
                        OutlinedButton(onClick={
                            if(action=="delete")pendingDelete=selected
                            else {onBulk(selected,action);selected=emptySet()}
                        }){Text(label)}
                    }
                }
            }
            if (visibleEntries.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(if (loading) "Syncing…" else if (query.isBlank()) "Nothing here yet." else "No matching entries.")
                }
            } else {
                LazyColumn {
                    items(visibleEntries, key = { it.id }) { entry ->
                        Row(
                            Modifier.fillMaxWidth().clickable { onSelect(entry) }.padding(start = 18.dp, top = 14.dp, bottom = 14.dp),
                            verticalAlignment = Alignment.Top,
                        ) {
                            Checkbox(
                                checked=entry.id in selected,
                                onCheckedChange={checked -> selected=if(checked)selected+entry.id else selected-entry.id},
                            )
                            Column(Modifier.weight(1f)) {
                                Text(entry.title.ifBlank { entry.category.uppercase() }, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.Bold)
                                Text(entry.transcription.ifBlank { "Audio recording" }, maxLines = 3, style = MaterialTheme.typography.bodyLarge)
                                Text(formatDate(entry.createdAt), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            IconButton(onClick = { onStar(entry) }) {
                                val starred=entry.starred==1
                                Icon(
                                    imageVector=if(starred)Icons.Default.Star else Icons.Outlined.Star,
                                    contentDescription=if(starred)"Unstar note" else "Star note",
                                    tint=if(starred) {
                                        androidx.compose.ui.graphics.Color(0xFFFFCA28)
                                    } else {
                                        androidx.compose.ui.graphics.Color.White
                                    },
                                )
                            }
                        }
                        HorizontalDivider()
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun EntryScreen(
    entry: Entry,
    groups: List<NoteGroup>,
    loading: Boolean,
    onBack: () -> Unit,
    onSave: (EntryUpdate) -> Unit,
    onDelete: () -> Unit,
    onAssignGroup: (String?) -> Unit,
    onDownloadAudio: (Uri) -> Unit,
    audioSource: Pair<String, String>?,
) {
    var title by remember(entry.id) { mutableStateOf(entry.title) }
    var text by remember(entry.id) { mutableStateOf(entry.transcription) }
    var tags by remember(entry.id) { mutableStateOf(entry.tags) }
    var confirmDelete by remember { mutableStateOf(false) }
    var showPayload by remember { mutableStateOf(false) }
    val audioDownload=rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument(entry.audioMime ?: "audio/*")) { uri ->
        if(uri!=null)onDownloadAudio(uri)
    }
    if(confirmDelete) AlertDialog(
        onDismissRequest={confirmDelete=false},
        title={Text("Delete this entry?")},
        text={Text("This permanently removes the note and its stored audio.")},
        confirmButton={TextButton(onClick={confirmDelete=false;onDelete()}){Text("Delete")}},
        dismissButton={TextButton(onClick={confirmDelete=false}){Text("Cancel")}},
    )
    if(showPayload) AlertDialog(
        onDismissRequest={showPayload=false},
        title={Text("Original webhook payload")},
        text={
            val pretty=remember(entry.payloadJson){runCatching{Json{prettyPrint=true}.encodeToString(kotlinx.serialization.json.JsonElement.serializer(),Json.parseToJsonElement(entry.payloadJson))}.getOrDefault(entry.payloadJson)}
            Text(pretty,Modifier.verticalScroll(rememberScrollState()))
        },
        confirmButton={TextButton(onClick={showPayload=false}){Text("Close")}},
    )
    Scaffold(topBar = {
        TopAppBar(
            title = { Text("Edit entry") },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } },
            actions = {
                IconButton(onClick = { onSave(EntryUpdate(title = title, transcription = text, tags = tags)) }, enabled = !loading) { Icon(Icons.Default.Check, "Save") }
            },
        )
    }) { padding ->
        Column(
            Modifier.padding(padding).verticalScroll(rememberScrollState())
                .padding(start=18.dp,end=18.dp,top=18.dp,bottom=40.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            OutlinedTextField(title, { title = it }, label = { Text("Title") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(text, { text = it }, label = { Text("Transcription") }, minLines = 7, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(tags, { tags = it }, label = { Text("Tags") }, modifier = Modifier.fillMaxWidth())
            Text("Category", style = MaterialTheme.typography.labelMedium)
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("note","task","idea","question").forEach { category ->
                    FilterChip(selected = entry.category == category, onClick = { onSave(EntryUpdate(category = category)) }, label = { Text(category) })
                }
            }
            Text("Group",style=MaterialTheme.typography.labelMedium)
            Row(Modifier.horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                FilterChip(selected=entry.groupName==null,onClick={onAssignGroup(null)},label={Text("Standalone")})
                groups.forEach { group ->
                    FilterChip(selected=entry.groupName==group.name,onClick={onAssignGroup(group.name)},label={Text(group.name)})
                }
            }
            Text(formatDate(entry.createdAt), style = MaterialTheme.typography.labelMedium)
            Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Processed")
                    Text(if(entry.processed==1)"This note has been reviewed" else "This note still needs review",style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Switch(checked=entry.processed==1,onCheckedChange={onSave(EntryUpdate(processed=it))})
            }
            OutlinedButton(onClick={showPayload=true}){Text("View original payload")}
            if (entry.audioPath != null && audioSource != null) {
                AudioPlayer(audioSource.first,audioSource.second)
                OutlinedButton(onClick={audioDownload.launch(entry.audioPath)}){Text("Download audio")}
            }
            Row(Modifier.fillMaxWidth(),horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedButton(
                    onClick = { onSave(EntryUpdate(archived = entry.archived==0)) },
                    modifier=Modifier.weight(1f),
                ) { Icon(Icons.Default.Archive, null); Text(if(entry.archived==1)" Restore" else " Archive") }
                OutlinedButton(
                    onClick = {confirmDelete=true},
                    modifier=Modifier.weight(1f),
                ) { Icon(Icons.Default.Delete, null); Text(" Delete") }
            }
        }
    }
}

@Composable
private fun AudioPlayer(url: String, token: String) {
    val context = LocalContext.current
    var player by remember { mutableStateOf<MediaPlayer?>(null) }
    var playing by remember { mutableStateOf(false) }
    var speed by remember { mutableStateOf(1f) }
    DisposableEffect(Unit) { onDispose { player?.release() } }
    OutlinedButton(onClick = {
        if (playing) {
            player?.pause()
            playing = false
        } else if (player != null) {
            player?.start()
            playing = true
        } else {
            player = MediaPlayer().apply {
                setDataSource(context, Uri.parse(url), mapOf("Authorization" to "Bearer $token"))
                setOnPreparedListener { it.playbackParams=it.playbackParams.setSpeed(speed);it.start();playing = true }
                setOnCompletionListener { playing = false; it.seekTo(0) }
                prepareAsync()
            }
        }
    }) { Text(if (playing) "Pause audio" else "Play audio") }
    Row(Modifier.horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(6.dp)) {
        listOf(.75f,1f,1.5f,2f).forEach { value ->
            FilterChip(selected=speed==value,onClick={
                speed=value
                player?.let{if(it.isPlaying)it.playbackParams=it.playbackParams.setSpeed(value)}
            },label={Text("${value}×")})
        }
    }
}

@Composable
private fun LocalAudioPreview(file: File) {
    var player by remember(file) { mutableStateOf<MediaPlayer?>(null) }
    var playing by remember(file) { mutableStateOf(false) }
    DisposableEffect(file) { onDispose { player?.release() } }
    OutlinedButton(onClick={
        if(playing){player?.pause();playing=false}
        else if(player!=null){player?.start();playing=true}
        else player=MediaPlayer().apply {
            setDataSource(file.absolutePath)
            setOnPreparedListener{it.start();playing=true}
            setOnCompletionListener{playing=false;it.seekTo(0)}
            prepareAsync()
        }
    }) { Text(if(playing)"Pause recording" else "Preview recording") }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun GroupsScreen(
    groups: List<NoteGroup>,
    loading: Boolean,
    onBack: () -> Unit,
    onOpen: (String) -> Unit,
    onToggle: (NoteGroup) -> Unit,
    onRename: (NoteGroup,String) -> Unit,
    onAliases: (String) -> Unit,
    onRemove: (NoteGroup) -> Unit,
    onSuggestions: () -> Unit,
) {
    var renameGroup by remember { mutableStateOf<NoteGroup?>(null) }
    var removeGroup by remember { mutableStateOf<NoteGroup?>(null) }
    var renameValue by remember { mutableStateOf("") }
    renameGroup?.let { group ->
        AlertDialog(
            onDismissRequest={renameGroup=null},
            title={Text("Rename ${group.name}")},
            text={OutlinedTextField(renameValue,{renameValue=it},label={Text("Canonical name")},singleLine=true)},
            confirmButton={TextButton(onClick={onRename(group,renameValue);renameGroup=null}){Text("Rename")}},
            dismissButton={TextButton(onClick={renameGroup=null}){Text("Cancel")}},
        )
    }
    removeGroup?.let { group ->
        AlertDialog(
            onDismissRequest={removeGroup=null},
            title={Text("Remove ${group.name}?")},
            text={Text(if(group.entries>0)"Its ${group.entries} entries will be preserved as standalone notes." else "This empty group will be permanently removed.")},
            confirmButton={TextButton(onClick={onRemove(group);removeGroup=null}){Text("Remove group")}},
            dismissButton={TextButton(onClick={removeGroup=null}){Text("Cancel")}},
        )
    }
    Scaffold(topBar = {
        TopAppBar(
            title = { Text("Note groups") },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } },
            actions = { Button(onClick = onSuggestions) { Text("Suggestions") } },
        )
    }) { padding ->
        if (groups.isEmpty()) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                Text(if (loading) "Loading groups…" else "No groups have been created.")
            }
        } else {
            LazyColumn(Modifier.padding(padding)) {
                items(groups, key = { it.name }) { group ->
                    Column(Modifier.fillMaxWidth().clickable { onOpen(group.name) }.padding(18.dp)) {
                        Column {
                            Text(group.name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                            Text("${group.entries} entries${if (group.archived == 1) " • archived" else ""}", color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Row(Modifier.horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(onClick={renameGroup=group;renameValue=group.name}){Text("Rename")}
                            OutlinedButton(onClick={onAliases(group.name)}){Text("Aliases")}
                            OutlinedButton(onClick = { onToggle(group) }) { Text(if(group.archived==1) "Reopen" else "Archive") }
                            OutlinedButton(onClick={removeGroup=group}){Text("Remove")}
                        }
                    }
                    HorizontalDivider()
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun GroupTimelineScreen(
    timeline: GroupTimeline?,
    loading: Boolean,
    onBack: () -> Unit,
    onSave: (Entry,EntryUpdate) -> Unit,
    audioSource: (Entry) -> Pair<String,String>?,
    onExport: (String,String,Uri) -> Unit,
) {
    val groupName=timeline?.group?.name
    val markdownExport=rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/markdown")) { uri ->
        if(uri!=null&&groupName!=null)onExport(groupName,"markdown",uri)
    }
    val jsonExport=rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/json")) { uri ->
        if(uri!=null&&groupName!=null)onExport(groupName,"json",uri)
    }
    val zipExport=rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        if(uri!=null&&groupName!=null)onExport(groupName,"zip",uri)
    }
    Scaffold(topBar = {
        TopAppBar(
            title = { Text(timeline?.group?.name ?: "Group timeline") },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } },
            actions={
                TextButton(onClick={groupName?.let{markdownExport.launch("index-inbox-${it.lowercase()}.md")}}){Text("MD")}
                TextButton(onClick={groupName?.let{jsonExport.launch("index-inbox-${it.lowercase()}.json")}}){Text("JSON")}
                TextButton(onClick={groupName?.let{zipExport.launch("index-inbox-${it.lowercase()}.zip")}}){Text("ZIP")}
            },
        )
    }) { padding ->
        val items = timeline?.items.orEmpty()
        if (items.isEmpty()) Box(Modifier.fillMaxSize().padding(padding), contentAlignment=Alignment.Center) {
            Text(if(loading) "Loading timeline…" else "This group has no entries.")
        } else LazyColumn(Modifier.padding(padding)) {
            items(items,key={it.id}) { entry ->
                EditableTimelineEntry(entry,onSave,audioSource(entry))
                HorizontalDivider()
            }
        }
    }
}

@Composable
private fun EditableTimelineEntry(entry: Entry,onSave: (Entry,EntryUpdate) -> Unit,audioSource: Pair<String,String>?) {
    var text by remember(entry.id,entry.transcription){mutableStateOf(entry.transcription)}
    var tags by remember(entry.id,entry.tags){mutableStateOf(entry.tags)}
    var category by remember(entry.id,entry.category){mutableStateOf(entry.category)}
    Column(Modifier.fillMaxWidth().padding(18.dp),verticalArrangement=Arrangement.spacedBy(8.dp)) {
        Text(formatDate(entry.recordedAt ?: entry.createdAt),style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedTextField(text,{text=it},label={Text("Transcription")},modifier=Modifier.fillMaxWidth(),minLines=3)
        OutlinedTextField(tags,{tags=it},label={Text("Tags")},modifier=Modifier.fillMaxWidth())
        Row(Modifier.horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(8.dp)) {
            listOf("note","task","idea","question").forEach { value ->
                FilterChip(selected=category==value,onClick={category=value},label={Text(value)})
            }
        }
        if(entry.audioPath!=null&&audioSource!=null)AudioPlayer(audioSource.first,audioSource.second)
        Button(onClick={onSave(entry,EntryUpdate(transcription=text,tags=tags,category=category))}){Text("Save changes")}
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AliasesScreen(
    aliases: GroupAliases?,
    loading: Boolean,
    onBack: () -> Unit,
    onAdd: (String) -> Unit,
    onRemove: (String) -> Unit,
) {
    var addDialog by remember { mutableStateOf(false) }
    var value by remember { mutableStateOf("") }
    if(addDialog) AlertDialog(
        onDismissRequest={addDialog=false},
        title={Text("Add spoken alias")},
        text={OutlinedTextField(value,{value=it},label={Text("Alias")},singleLine=true)},
        confirmButton={TextButton(onClick={if(value.isNotBlank())onAdd(value.trim());addDialog=false;value=""}){Text("Add")}},
        dismissButton={TextButton(onClick={addDialog=false}){Text("Cancel")}},
    )
    Scaffold(topBar={
        TopAppBar(
            title={Text(aliases?.let{"Aliases for ${it.group}"} ?: "Aliases")},
            navigationIcon={IconButton(onClick=onBack){Icon(Icons.AutoMirrored.Filled.ArrowBack,"Back")}},
            actions={Button(onClick={addDialog=true}){Text("Add")}},
        )
    }) { padding ->
        val items=aliases?.aliases.orEmpty()
        if(items.isEmpty()) Box(Modifier.fillMaxSize().padding(padding),contentAlignment=Alignment.Center){
            Text(if(loading)"Loading aliases…" else "No aliases.")
        } else LazyColumn(Modifier.padding(padding)) {
            items(items,key={it}) { alias ->
                Row(Modifier.fillMaxWidth().padding(18.dp),verticalAlignment=Alignment.CenterVertically) {
                    Text(alias,Modifier.weight(1f))
                    if(alias!=aliases?.group?.lowercase()) TextButton(onClick={onRemove(alias)}){Text("Remove")}
                }
                HorizontalDivider()
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SuggestionsScreen(
    suggestions: List<GroupSuggestion>,
    loading: Boolean,
    onBack: () -> Unit,
    onResolve: (GroupSuggestion,String) -> Unit,
) {
    Scaffold(topBar = {
        TopAppBar(
            title={Text("Suggested groups")},
            navigationIcon={IconButton(onClick=onBack){Icon(Icons.AutoMirrored.Filled.ArrowBack,"Back")}},
        )
    }) { padding ->
        if(suggestions.isEmpty()) Box(Modifier.fillMaxSize().padding(padding),contentAlignment=Alignment.Center){
            Text(if(loading) "Loading suggestions…" else "No suggestions to review.")
        } else LazyColumn(Modifier.padding(padding)) {
            items(suggestions,key={it.entryId}) { suggestion ->
                Column(Modifier.fillMaxWidth().padding(18.dp),verticalArrangement=Arrangement.spacedBy(8.dp)) {
                    Text("Suggested: ${suggestion.group}",fontWeight=FontWeight.Bold,color=MaterialTheme.colorScheme.primary)
                    Text(suggestion.transcription)
                    Text("Heard identifier ${suggestion.candidate}",style=MaterialTheme.typography.labelSmall)
                    Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                        Button(onClick={onResolve(suggestion,"accept")}){Text("Accept")}
                        OutlinedButton(onClick={onResolve(suggestion,"dismiss")}){Text("Dismiss")}
                    }
                }
                HorizontalDivider()
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun StatusScreen(
    status: ServerStatus?,
    loading: Boolean,
    devices: List<DeviceSession>,
    appUpdate: AndroidUpdate?,
    updateDownloadProgress: Int?,
    indexRingIntegration: IndexRingIntegration?,
    indexRingSecret: String?,
    notificationsEnabled: Boolean,
    instantNotifications: Boolean,
    widgetCaptureMode: String,
    widgetCaptureCategory: String,
    onBack: () -> Unit,
    onBackup: () -> Unit,
    onRetention: (Int) -> Unit,
    onExport: (String,Uri) -> Unit,
    onDownloadBackup: (Uri) -> Unit,
    onBackupHook: () -> Unit,
    onNotifications: (Boolean) -> Unit,
    onInstantNotifications: (Boolean) -> Unit,
    onWidgetCaptureMode: (String) -> Unit,
    onWidgetCaptureCategory: (String) -> Unit,
    onRevokeOthers: () -> Unit,
    onCheckUpdate: () -> Unit,
    onInstallUpdate: () -> Unit,
    onRevealIndexRing: (String) -> Unit,
    onRotateIndexRing: (String) -> Unit,
    onTestIndexRing: () -> Unit,
) {
    var retentionDialog by remember { mutableStateOf(false) }
    var days by remember { mutableStateOf("30") }
    var integrationAction by remember { mutableStateOf<String?>(null) }
    var integrationPassword by remember { mutableStateOf("") }
    val clipboard=LocalClipboardManager.current
    val markdownExport=rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/markdown")) { uri -> if(uri!=null)onExport("markdown",uri) }
    val jsonExport=rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/json")) { uri -> if(uri!=null)onExport("json",uri) }
    val zipExport=rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri -> if(uri!=null)onExport("zip",uri) }
    val backupDownload=rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri -> if(uri!=null)onDownloadBackup(uri) }
    if(retentionDialog) AlertDialog(
        onDismissRequest={retentionDialog=false},
        title={Text("Remove old audio?")},
        text={
            Column(verticalArrangement=Arrangement.spacedBy(8.dp)) {
                Text("Audio files older than this many days will be permanently removed. Their text entries remain.")
                OutlinedTextField(days,{days=it.filter(Char::isDigit)},label={Text("Days")},singleLine=true)
            }
        },
        confirmButton={TextButton(onClick={
            days.toIntOrNull()?.takeIf{it>=1}?.let(onRetention)
            retentionDialog=false
        }){Text("Remove audio")}},
        dismissButton={TextButton(onClick={retentionDialog=false}){Text("Cancel")}},
    )
    if(integrationAction!=null) AlertDialog(
        onDismissRequest={integrationAction=null;integrationPassword=""},
        title={Text(if(integrationAction=="rotate")"Rotate webhook secret?" else "Reveal webhook secret?")},
        text={Column(verticalArrangement=Arrangement.spacedBy(8.dp)){
            if(integrationAction=="rotate")Text("The old value will stop working immediately. Update the Pebble app after rotation.")
            if(indexRingIntegration?.requiresPassword==true)OutlinedTextField(
                integrationPassword,{integrationPassword=it},label={Text("Current account password")},
                visualTransformation=PasswordVisualTransformation(),singleLine=true,
            )
        }},
        confirmButton={TextButton(onClick={
            if(integrationAction=="rotate")onRotateIndexRing(integrationPassword) else onRevealIndexRing(integrationPassword)
            integrationAction=null;integrationPassword=""
        },enabled=indexRingIntegration?.requiresPassword!=true||integrationPassword.isNotBlank()){
            Text(if(integrationAction=="rotate")"Rotate" else "Reveal")
        }},
        dismissButton={TextButton(onClick={integrationAction=null;integrationPassword=""}){Text("Cancel")}},
    )
    Scaffold(topBar={
        TopAppBar(
            title={Text("Storage & backup")},
            navigationIcon={IconButton(onClick=onBack){Icon(Icons.AutoMirrored.Filled.ArrowBack,"Back")}},
        )
    }) { padding ->
        if(status==null) Box(Modifier.fillMaxSize().padding(padding),contentAlignment=Alignment.Center){
            Text(if(loading)"Loading server status…" else "Status unavailable.")
        } else Column(Modifier.padding(padding).verticalScroll(rememberScrollState()).padding(20.dp),verticalArrangement=Arrangement.spacedBy(14.dp)) {
            Text("${status.entries} entries",style=MaterialTheme.typography.headlineSmall,fontWeight=FontWeight.Bold)
            Text("${status.audioEntries} with audio • ${formatBytes(status.audioBytes)} audio")
            Text("${formatBytes(status.databaseBytes)} database")
            HorizontalDivider()
            Text("Transcription",fontWeight=FontWeight.Bold)
            Text(if(status.transcriptionEnabled)"Enabled • ${status.transcriptionModel}" else "Disabled")
            HorizontalDivider()
            Text("Index Ring integration",fontWeight=FontWeight.Bold)
            Text("Add this URL and the X-Webhook-Secret header to the Index webhook in the Pebble app.")
            Text(indexRingIntegration?.webhookUrl ?: "Integration details unavailable",style=MaterialTheme.typography.bodySmall)
            OutlinedButton(
                onClick={indexRingIntegration?.webhookUrl?.let{clipboard.setText(AnnotatedString(it))}},
                enabled=indexRingIntegration!=null,
            ){Text("Copy webhook URL")}
            Text(indexRingSecret ?: indexRingIntegration?.maskedSecret.orEmpty(),style=MaterialTheme.typography.bodySmall)
            Row(Modifier.horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick={integrationAction="reveal"},enabled=indexRingIntegration?.configured==true){Text("Reveal")}
                OutlinedButton(onClick={indexRingSecret?.let{clipboard.setText(AnnotatedString(it))}},enabled=!indexRingSecret.isNullOrBlank()){Text("Copy secret")}
                OutlinedButton(onClick=onTestIndexRing,enabled=!indexRingSecret.isNullOrBlank()){Text("Test")}
                OutlinedButton(onClick={integrationAction="rotate"},enabled=indexRingIntegration!=null){Text("Rotate")}
            }
            HorizontalDivider()
            Text("App updates",fontWeight=FontWeight.Bold)
            Text("Installed: ${BuildConfig.VERSION_NAME}")
            if(appUpdate?.available==true) {
                Text("Server release: ${appUpdate.versionName} • ${formatBytes(appUpdate.bytes)}")
            } else Text("No self-hosted release configured",color=MaterialTheme.colorScheme.onSurfaceVariant)
            Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick=onCheckUpdate,enabled=!loading){Text("Check")}
                if(appUpdate?.available==true&&appUpdate.versionCode>BuildConfig.VERSION_CODE) {
                    Button(onClick=onInstallUpdate,enabled=!loading){
                        Text(updateDownloadProgress?.let{"Downloading $it%"} ?: "Download & install")
                    }
                }
            }
            updateDownloadProgress?.let { progress ->
                LinearProgressIndicator(
                    progress={progress/100f},
                    modifier=Modifier.fillMaxWidth(),
                )
                Text(
                    if(progress<100)"Downloading update… $progress%" else "Download complete. Verifying installer…",
                    style=MaterialTheme.typography.labelSmall,
                )
            }
            HorizontalDivider()
            Text("Notifications",fontWeight=FontWeight.Bold)
            Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
                Text("Activity notifications",Modifier.weight(1f))
                Switch(checked=notificationsEnabled,onCheckedChange=onNotifications)
            }
            Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Instant self-hosted connection")
                    Text("Shows a permanent connected notification",style=MaterialTheme.typography.labelSmall)
                }
                Switch(checked=instantNotifications,onCheckedChange=onInstantNotifications,enabled=notificationsEnabled)
            }
            HorizontalDivider()
            Text("Home-screen audio widget",fontWeight=FontWeight.Bold)
            Text("Add the Index Inbox widget from your launcher. Tap once to record and again to stop.")
            Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                FilterChip(
                    selected=widgetCaptureMode=="instant",
                    onClick={onWidgetCaptureMode("instant")},
                    label={Text("Instant save")},
                )
                FilterChip(
                    selected=widgetCaptureMode=="review",
                    onClick={onWidgetCaptureMode("review")},
                    label={Text("Review first")},
                )
            }
            Text(
                if(widgetCaptureMode=="instant")"The second tap uploads and saves immediately."
                else "The second tap opens the transcript so you can correct it before saving.",
                style=MaterialTheme.typography.labelSmall,
            )
            Text("Default category",fontWeight=FontWeight.Bold)
            Row(Modifier.horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                listOf("note","task","idea","question").forEach { category ->
                    FilterChip(
                        selected=widgetCaptureCategory==category,
                        onClick={onWidgetCaptureCategory(category)},
                        label={Text(category.replaceFirstChar(Char::uppercase))},
                    )
                }
            }
            HorizontalDivider()
            Text("Signed-in devices",fontWeight=FontWeight.Bold)
            devices.forEach { device ->
                Text("${device.deviceName}${if(device.current)" • this device" else ""}\nLast used ${formatDate(device.lastSeenAt)}")
            }
            OutlinedButton(onClick=onRevokeOthers,enabled=!loading&&devices.any{!it.current}){Text("Revoke other devices")}
            HorizontalDivider()
            Text("Verified backups",fontWeight=FontWeight.Bold)
            Text(status.latestVerifiedBackup?.let{"Latest: ${it.archiveName} • ${formatDate(it.completedAt ?: it.requestedAt)}"} ?: "No verified backup available")
            Button(onClick=onBackup,enabled=!loading){Text(if(loading)"Working…" else "Create verified backup")}
            OutlinedButton(
                onClick={backupDownload.launch(status.latestVerifiedBackup?.archiveName ?: "index-inbox-backup.zip")},
                enabled=!loading&&status.latestVerifiedBackup!=null,
            ){Text("Download latest verified backup")}
            if(status.lastBackupHook) OutlinedButton(onClick=onBackupHook,enabled=!loading){Text("Trigger external backup hook")}
            HorizontalDivider()
            Text("Export all entries",fontWeight=FontWeight.Bold)
            Row(Modifier.horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick={markdownExport.launch("index-inbox.md")}){Text("Markdown")}
                OutlinedButton(onClick={jsonExport.launch("index-inbox.json")}){Text("JSON")}
                OutlinedButton(onClick={zipExport.launch("index-inbox.zip")}){Text("ZIP + audio")}
            }
            HorizontalDivider()
            Text("Maintenance",fontWeight=FontWeight.Bold)
            OutlinedButton(onClick={retentionDialog=true},enabled=!loading){Text("Remove old audio…")}
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ActivityScreen(activity: List<ActivityItem>, loading: Boolean, onBack: () -> Unit) {
    Scaffold(topBar = {
        TopAppBar(
            title = { Text("Recent activity") },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } },
        )
    }) { padding ->
        if (activity.isEmpty()) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                Text(if (loading) "Loading activity…" else "No activity yet.")
            }
        } else {
            LazyColumn(Modifier.padding(padding)) {
                items(activity, key = { it.id }) { item ->
                    Column(Modifier.fillMaxWidth().padding(18.dp)) {
                        Text(item.kind.replace('_',' '), fontWeight = FontWeight.Bold)
                        Text(item.message)
                        Text(formatDate(item.createdAt), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    HorizontalDivider()
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PendingCapturesScreen(
    captures: List<PendingCapture>,
    loading: Boolean,
    onBack: () -> Unit,
    onSave: (PendingCapture,String,String,String) -> Unit,
    onDiscard: (PendingCapture) -> Unit,
    onRetry: () -> Unit,
) {
    Scaffold(topBar={
        TopAppBar(
            title={Text("Pending captures")},
            navigationIcon={IconButton(onClick=onBack){Icon(Icons.AutoMirrored.Filled.ArrowBack,"Back")}},
            actions={Button(onClick=onRetry,enabled=!loading&&captures.isNotEmpty()){Text("Retry all")}},
        )
    }) { padding ->
        if(captures.isEmpty())Box(Modifier.fillMaxSize().padding(padding),contentAlignment=Alignment.Center){
            Text(if(loading)"Retrying…" else "No captures are waiting to upload.")
        } else LazyColumn(Modifier.padding(padding)) {
            items(captures,key={it.id}) { capture ->
                PendingCaptureCard(capture,onSave,onDiscard)
                HorizontalDivider()
            }
        }
    }
}

@Composable
private fun PendingCaptureCard(
    capture: PendingCapture,
    onSave: (PendingCapture,String,String,String) -> Unit,
    onDiscard: (PendingCapture) -> Unit,
) {
    var title by remember(capture.id,capture.title){mutableStateOf(capture.title)}
    var text by remember(capture.id,capture.transcription){mutableStateOf(capture.transcription)}
    var category by remember(capture.id,capture.category){mutableStateOf(capture.category)}
    var confirmDiscard by remember{mutableStateOf(false)}
    if(confirmDiscard)AlertDialog(
        onDismissRequest={confirmDiscard=false},
        title={Text("Discard pending capture?")},
        text={Text("The queued note${if(capture.audioPath!=null)" and recording" else ""} will be permanently removed.")},
        confirmButton={TextButton(onClick={confirmDiscard=false;onDiscard(capture)}){Text("Discard")}},
        dismissButton={TextButton(onClick={confirmDiscard=false}){Text("Cancel")}},
    )
    Column(Modifier.fillMaxWidth().padding(18.dp),verticalArrangement=Arrangement.spacedBy(8.dp)) {
        Text(formatDate(java.time.Instant.ofEpochMilli(capture.createdAt).toString()),style=MaterialTheme.typography.labelSmall)
        if(capture.audioPath!=null)Text("Audio attached",color=MaterialTheme.colorScheme.primary,fontWeight=FontWeight.Bold)
        OutlinedTextField(title,{title=it},label={Text("Title")},modifier=Modifier.fillMaxWidth())
        OutlinedTextField(text,{text=it},label={Text("Transcription")},modifier=Modifier.fillMaxWidth(),minLines=3)
        Row(Modifier.horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(8.dp)) {
            listOf("note","task","idea","question").forEach { value ->
                FilterChip(selected=category==value,onClick={category=value},label={Text(value)})
            }
        }
        if(capture.lastError.isNotBlank())Text(capture.lastError,style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.error)
        Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
            Button(onClick={onSave(capture,title,text,category)}){Text("Save edits")}
            OutlinedButton(onClick={confirmDiscard=true}){Text("Discard")}
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CaptureScreen(
    initial: String,
    initialAudio: File? = null,
    initialStatus: String = "",
    initialCategory: String = "note",
    loading: Boolean,
    onClose: () -> Unit,
    onSave: (String, String, String) -> Unit,
    onSaveAudio: (String, String, String, File) -> Unit,
    onTranscribe: (File, (Result<String>) -> Unit) -> Unit,
) {
    val context = LocalContext.current
    var title by remember { mutableStateOf("") }
    var text by remember(initial) { mutableStateOf(initial) }
    var category by remember(initialCategory) { mutableStateOf(initialCategory) }
    var recorder by remember { mutableStateOf<MediaRecorder?>(null) }
    var recordingFile by remember(initialAudio) { mutableStateOf(initialAudio) }
    var isRecording by remember { mutableStateOf(false) }
    var status by remember(initialStatus) { mutableStateOf(initialStatus) }
    fun stopRecording() {
        runCatching { recorder?.stop() }
        recorder?.release()
        recorder = null
        isRecording = false
        status = "Transcribing locally on your server…"
        recordingFile?.let { file ->
            onTranscribe(file) { result ->
                result.onSuccess {
                    text = it
                    status = "Transcription ready. Review or correct it before saving."
                }.onFailure {
                    status = "Transcription failed: ${it.message}. You can retry by recording again."
                }
            }
        }
    }
    fun startRecording() {
        val file = File.createTempFile("index-recording-", ".m4a", context.cacheDir)
        recordingFile?.delete()
        recordingFile = file
        @Suppress("DEPRECATION")
        recorder = MediaRecorder().apply {
            setAudioSource(MediaRecorder.AudioSource.MIC)
            setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            setAudioEncodingBitRate(96_000)
            setAudioSamplingRate(44_100)
            setOutputFile(file.absolutePath)
            prepare()
            start()
        }
        isRecording = true
        status = "Recording…"
    }
    LaunchedEffect(initialAudio) {
        initialAudio?.let { file ->
            status = "Transcribing on your server…"
            onTranscribe(file) { result ->
                result.onSuccess {
                    text = it
                    status = "Transcription ready. Review or correct it before saving."
                }.onFailure {
                    status = "Transcription failed: ${it.message}. The recording is still available."
                }
            }
        }
    }
    val microphonePermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) {
            CaptureWidgetProvider.updateAll(context)
            runCatching { startRecording() }.onFailure { status = "Could not start recording: ${it.message}" }
        }
        else status = "Microphone permission was denied."
    }
    DisposableEffect(Unit) {
        onDispose {
            if (isRecording) runCatching { recorder?.stop() }
            recorder?.release()
            recordingFile?.delete()
        }
    }
    Scaffold(topBar = {
        TopAppBar(
            title = { Text("New capture") },
            navigationIcon = { IconButton(onClick = onClose) { Icon(Icons.Default.Close, "Close") } },
        )
    }) { padding ->
        Column(
            Modifier.padding(padding).verticalScroll(rememberScrollState()).padding(18.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            OutlinedTextField(title, { title = it }, label = { Text("Title (optional)") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(text, { text = it }, label = { Text("What do you want to remember?") }, minLines = 9, modifier = Modifier.fillMaxWidth())
            Row(Modifier.horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                listOf("note","task","idea","question").forEach { value ->
                    FilterChip(selected=category==value,onClick={category=value},label={Text(value)})
                }
            }
            OutlinedButton(
                onClick = {
                    if (isRecording) stopRecording()
                    else microphonePermission.launch(Manifest.permission.RECORD_AUDIO)
                },
                enabled = !loading,
                modifier = Modifier.fillMaxWidth(),
            ) { Text(if (isRecording) "Stop recording" else if (recordingFile != null) "Record again" else "Record audio") }
            if (status.isNotBlank()) Text(status, style = MaterialTheme.typography.bodySmall)
            if (recordingFile != null) {
                LocalAudioPreview(recordingFile!!)
                OutlinedButton(onClick = {
                    recordingFile?.delete()
                    recordingFile = null
                    status = ""
                }) { Text("Discard recording") }
            }
            Button(
                onClick = {
                    if (isRecording) stopRecording()
                    val audio = recordingFile
                    if (audio != null) onSaveAudio(title,text,category,audio) else onSave(title,text,category)
                },
                enabled = !loading && !isRecording && (text.isNotBlank() || recordingFile != null),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(if (loading) "Saving and transcribing…" else "Save to inbox")
            }
        }
    }
}

private fun formatDate(value: String): String = runCatching {
    OffsetDateTime.parse(value).format(DateTimeFormatter.ofPattern("d MMM yyyy, HH:mm"))
}.getOrDefault(value)

internal fun downloadProgress(downloaded:Long,total:Long):Int =
    if(total<=0)0 else (downloaded*100/total).coerceIn(0,100).toInt()

private fun formatBytes(value: Long): String = when {
    value < 1_024 -> "$value B"
    value < 1_048_576 -> "${value / 1_024} KB"
    else -> String.format("%.1f MB",value / 1_000_000.0)
}
