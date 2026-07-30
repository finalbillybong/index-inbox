package com.indexinbox.android

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

class AuthStore(context: Context) {
    private val prefs = EncryptedSharedPreferences.create(
        context,
        "index_credentials",
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    val serverUrl: String? get() = prefs.getString("server_url", null)
    val token: String? get() = prefs.getString("token", null)
    val darkMode: Boolean get() = prefs.getBoolean("dark_mode", false)
    val themeMode: String get() = prefs.getString("theme_mode", null)
        ?: if (prefs.contains("dark_mode")) if (darkMode) "dark" else "light" else "system"
    val notificationsEnabled: Boolean get() = prefs.getBoolean("notifications_enabled", true)
    val instantNotifications: Boolean get() = prefs.getBoolean("instant_notifications", true)
    val notificationPreview: Boolean get() = prefs.getBoolean("notification_preview", true)
    val notificationSound: Boolean get() = prefs.getBoolean("notification_sound", true)
    val notificationVibration: Boolean get() = prefs.getBoolean("notification_vibration", true)
    val quietHoursEnabled: Boolean get() = prefs.getBoolean("quiet_hours_enabled", false)
    val quietHoursStart: Int get() = prefs.getInt("quiet_hours_start", 22).coerceIn(0,23)
    val quietHoursEnd: Int get() = prefs.getInt("quiet_hours_end", 7).coerceIn(0,23)
    val widgetCaptureMode: String get() = prefs.getString("widget_capture_mode", "instant") ?: "instant"
    val widgetCaptureCategory: String get() = prefs.getString("widget_capture_category", "note") ?: "note"
    val widgetRecordingMinutes: Int get() = prefs.getInt("widget_recording_minutes", 5).coerceIn(1,15)

    fun save(serverUrl: String, token: String) {
        prefs.edit().putString("server_url", normalizeUrl(serverUrl)).putString("token", token).apply()
    }

    fun setDarkMode(enabled: Boolean) = prefs.edit().putBoolean("dark_mode", enabled).apply()
    fun setThemeMode(mode: String) = prefs.edit().putString("theme_mode", mode).apply()
    fun setNotificationsEnabled(enabled: Boolean) = prefs.edit().putBoolean("notifications_enabled", enabled).apply()
    fun setInstantNotifications(enabled: Boolean) = prefs.edit().putBoolean("instant_notifications", enabled).apply()
    fun setNotificationPreview(enabled: Boolean) = prefs.edit().putBoolean("notification_preview", enabled).apply()
    fun setNotificationSound(enabled: Boolean) = prefs.edit().putBoolean("notification_sound", enabled).apply()
    fun setNotificationVibration(enabled: Boolean) = prefs.edit().putBoolean("notification_vibration", enabled).apply()
    fun setQuietHours(enabled:Boolean,start:Int,end:Int) = prefs.edit()
        .putBoolean("quiet_hours_enabled",enabled)
        .putInt("quiet_hours_start",start.coerceIn(0,23))
        .putInt("quiet_hours_end",end.coerceIn(0,23))
        .apply()
    fun setWidgetCaptureMode(mode: String) = prefs.edit().putString("widget_capture_mode", mode).apply()
    fun setWidgetCaptureCategory(category: String) = prefs.edit().putString("widget_capture_category", category).apply()
    fun setWidgetRecordingMinutes(minutes:Int) = prefs.edit().putInt("widget_recording_minutes",minutes.coerceIn(1,15)).apply()

    fun clear() = prefs.edit().remove("server_url").remove("token").apply()

    companion object {
        fun normalizeUrl(value: String) = value.trim().trimEnd('/') + "/"
    }
}
