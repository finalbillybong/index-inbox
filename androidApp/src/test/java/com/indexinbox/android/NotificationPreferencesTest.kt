package com.indexinbox.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NotificationPreferencesTest {
    @Test
    fun quietHoursCanCrossMidnight() {
        assertTrue(isQuietHour(23,22,7))
        assertTrue(isQuietHour(6,22,7))
        assertFalse(isQuietHour(12,22,7))
    }

    @Test
    fun daytimeQuietHoursUseBoundedWindow() {
        assertTrue(isQuietHour(13,12,15))
        assertFalse(isQuietHour(15,12,15))
    }

    @Test
    fun alertPreferencesSelectSeparateChannels() {
        assertEquals("index_inbox_activity_sound_vibrate",notificationChannelId(true,true))
        assertEquals("index_inbox_activity_silent_still",notificationChannelId(false,false))
    }
}
