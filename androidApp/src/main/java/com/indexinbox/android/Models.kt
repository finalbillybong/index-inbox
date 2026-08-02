package com.indexinbox.android

import androidx.room.Entity
import androidx.room.PrimaryKey
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

@Serializable
data class DeviceLoginRequest(val username: String, val password: String, val deviceName: String)

@Serializable
data class DeviceLoginResponse(
    val authenticated: Boolean,
    val username: String,
    val token: String,
    val deviceName: String,
    val expiresAt: String,
)

@Serializable
data class EntryPage(val items: List<Entry>, val page: Int, val limit: Int, val total: Int, val pages: Int)

@Serializable
@Entity(tableName = "entries")
data class Entry(
    @PrimaryKey val id: String,
    @SerialName("created_at") val createdAt: String,
    @SerialName("recorded_at") val recordedAt: String? = null,
    val transcription: String = "",
    @SerialName("trigger_type") val triggerType: String? = null,
    @SerialName("audio_path") val audioPath: String? = null,
    @SerialName("audio_mime") val audioMime: String? = null,
    @SerialName("payload_json") val payloadJson: String = "{}",
    val starred: Int = 0,
    val processed: Int = 0,
    val completed: Int = 0,
    val tags: String = "",
    val title: String = "",
    val category: String = "note",
    val archived: Int = 0,
    @SerialName("source_key") val sourceKey: String? = null,
    @SerialName("group_name") val groupName: String? = null,
    @SerialName("collection_name") val collectionName: String? = null,
    @SerialName("due_at") val dueAt: String? = null,
    @SerialName("reminder_completed") val reminderCompleted: Int = 0,
    @SerialName("reminder_notify_before_minutes") val reminderNotifyBeforeMinutes: Int? = null,
)

@Serializable
data class EntryUpdate(
    val title: String? = null,
    val transcription: String? = null,
    val tags: String? = null,
    val category: String? = null,
    val starred: Boolean? = null,
    val processed: Boolean? = null,
    val completed: Boolean? = null,
    val archived: Boolean? = null,
    @SerialName("group_name") val groupName: String? = null,
    @SerialName("collection_name") val collectionName: String? = null,
    @SerialName("due_at") val dueAt: String? = null,
    @SerialName("reminder_completed") val reminderCompleted: Boolean? = null,
    @SerialName("reminder_notify_before_minutes") val reminderNotifyBeforeMinutes: Int? = null,
)

@Serializable
data class ManualCapture(
    val transcription: String,
    val title: String = "",
    val category: String = "note",
    val recordedAt: Long = System.currentTimeMillis(),
    val id: String? = null,
)

@Serializable
data class InterpretationRequest(
    val text: String,
    val referenceAt: String? = null,
    val collectionName: String? = null,
)

@Serializable
data class InterpretationResult(
    val version: String,
    val operation: String,
    val arguments: JsonObject,
    val confidence: Double,
    val explanation: String,
    val ambiguous: Boolean,
    val requiresConfirmation: Boolean,
)

@Serializable
data class ApiResult(val ok: Boolean = false, val id: String? = null, val error: String? = null)

@Serializable
data class ChangeFeed(val sequence: Long, val events: List<ChangeEvent> = emptyList())

@Serializable
data class ChangeEvent(
    val id: Long,
    @SerialName("created_at") val createdAt: String,
    val level: String,
    val kind: String,
    val message: String,
    val details: String = "",
)

@Serializable
data class TranscriptionResult(
    val ok: Boolean,
    val transcription: String,
    val language: String? = null,
    val duration: Double? = null,
)

@Serializable
data class NoteGroup(
    val name: String,
    @SerialName("created_at") val createdAt: String,
    val archived: Int,
    val entries: Int,
)

@Serializable
data class CreateCollectionRequest(val name: String)

@Serializable
data class ActivityItem(
    val id: Long,
    @SerialName("created_at") val createdAt: String,
    val level: String,
    val kind: String,
    val message: String,
    val details: String = "",
)

@Serializable
data class GroupTimeline(val group: TimelineGroup, val items: List<Entry>)

@Serializable
data class TimelineGroup(val name: String, @SerialName("created_at") val createdAt: String, val archived: Int)

@Serializable
data class GroupUpdate(val name: String? = null, val archived: Boolean? = null)

@Serializable
data class GroupUpdateResult(val ok: Boolean, val name: String, val archived: Boolean)

@Serializable
data class GroupAliases(val group: String, val aliases: List<String>)

@Serializable
data class AliasRequest(val alias: String)

@Serializable
data class GroupSuggestion(
    val entryId: String,
    val transcription: String,
    val createdAt: String,
    val group: String,
    val candidate: String,
    val suggestedText: String,
)

@Serializable
data class SuggestionRequest(val group: String)

@Serializable
data class BulkRequest(val ids: List<String>, val action: String)

@Serializable
data class ServerStatus(
    val entries: Int,
    val audioEntries: Int,
    val audioBytes: Long,
    val databaseBytes: Long,
    val transcriptionEnabled: Boolean,
    val transcriptionModel: String,
    val lastBackupHook: Boolean,
    val lastBackup: BackupRun? = null,
    val latestVerifiedBackup: BackupRun? = null,
)

@Serializable
data class BackupRun(
    val id: String,
    @SerialName("requested_at") val requestedAt: String,
    @SerialName("completed_at") val completedAt: String? = null,
    val status: String,
    @SerialName("archive_name") val archiveName: String? = null,
    @SerialName("archive_bytes") val archiveBytes: Long? = null,
    val error: String = "",
)

@Serializable
data class BackupResult(val ok: Boolean, val backup: BackupRun)

@Serializable
data class RetentionRequest(val audioDays: Int)

@Serializable
data class RetentionResult(val ok: Boolean, val removed: Int)

@Serializable
data class IndexRingIntegration(
    val webhookPath: String,
    val webhookUrl: String,
    val configured: Boolean,
    val maskedSecret: String,
    val requiresPassword: Boolean,
)

@Serializable
data class IntegrationPasswordRequest(val password: String = "")

@Serializable
data class IntegrationSecret(val secret: String)

@Serializable
data class DeviceSession(
    val deviceName: String,
    val createdAt: String,
    val lastSeenAt: String,
    val expiresAt: String,
    val current: Boolean,
)

@Serializable
data class RevokeDevicesResult(val ok: Boolean, val revoked: Int)

@Serializable
data class AndroidUpdate(
    val available: Boolean,
    val versionCode: Int = 0,
    val versionName: String = "",
    val bytes: Long = 0,
    val sha256: String = "",
)
