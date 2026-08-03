package com.indexinbox.android

import org.junit.Assert.assertEquals
import org.junit.Test

class NotificationAndDownloadTest {
    @Test fun serverEventsUseUnderstandableNotificationGroups() {
        assertEquals("items",notificationPreferenceForEvent("capture_standalone"))
        assertEquals("commands",notificationPreferenceForEvent("interpreted_operation"))
        assertEquals("problems",notificationPreferenceForEvent("ingest_error"))
        assertEquals(null,notificationPreferenceForEvent("item_deleted"))
    }
    @Test fun ringOperationOutcomesUseClearNotificationTitles() {
        assertEquals("Ring command needs confirmation",ringOperationNotificationTitle("awaiting_confirmation"))
        assertEquals("Ring command needs review",ringOperationNotificationTitle("saved_plain_safely"))
        assertEquals("Ring command completed",ringOperationNotificationTitle("executed"))
    }
    private fun entry(transcription:String="",audioPath:String?=null)=Entry(
        id="entry-1",
        createdAt="2026-07-29T20:00:00Z",
        transcription=transcription,
        audioPath=audioPath,
    )

    @Test
    fun notificationShowsTrimmedNoteContent() {
        assertEquals("Remember the charger",notificationBody(entry("  Remember the charger  "),"Note received"))
    }

    @Test
    fun audioNotificationExplainsPendingTranscription() {
        assertEquals(
            "Audio Item received. Transcription may still be processing.",
            notificationBody(entry(audioPath="audio/entry-1.m4a"),"Item received"),
        )
    }

    @Test
    fun notificationFallsBackToEventMessage() {
        assertEquals("Note received",notificationBody(entry(),"Note received"))
    }

    @Test
    fun downloadProgressIsBounded() {
        assertEquals(0,downloadProgress(10,0))
        assertEquals(25,downloadProgress(25,100))
        assertEquals(100,downloadProgress(125,100))
    }
}
