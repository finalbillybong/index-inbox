package com.indexinbox.android

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class InterpretationContractTest {
    @Test
    fun versionOneContractDecodesForNativePreview() {
        val result=Json.decodeFromString<InterpretationResult>("""{
          "version":"1.0","operation":"set_reminder",
          "arguments":{"text":"call Mum","dueAt":"2026-08-03T08:00:00+00:00"},
          "confidence":0.99,"explanation":"Create an Item with the requested reminder time.",
          "ambiguous":false,"requiresConfirmation":false
        }""")
        assertEquals("1.0",result.version)
        assertEquals("set_reminder",result.operation)
        assertEquals("call Mum",result.arguments["text"]?.toString()?.trim('"'))
        assertEquals(0.99,result.confidence,0.0)
        assertFalse(result.ambiguous)
        assertFalse(result.requiresConfirmation)
    }

    @Test
    fun ambiguousCompletionRetainsCandidateList() {
        val result=Json.decodeFromString<InterpretationResult>("""{
          "version":"1.0","operation":"complete_item",
          "arguments":{"query":"milk","candidates":[{"id":"one","label":"Buy milk"},{"id":"two","label":"Order milk"}]},
          "confidence":0.45,"explanation":"More than one open Item matches.",
          "ambiguous":true,"requiresConfirmation":true
        }""")
        assertTrue(result.ambiguous)
        assertTrue(result.requiresConfirmation)
        assertTrue(result.arguments["candidates"].toString().contains("one"))
    }

    @Test
    fun previewWordingMatchesSharedClientContract() {
        val plain=InterpretationResult("1.0","create_item",kotlinx.serialization.json.buildJsonObject{},1.0,"Create a standalone Item.",false,false)
        val completion=plain.copy(operation="complete_item",confidence=.98,requiresConfirmation=true)
        val ambiguous=completion.copy(confidence=.45,ambiguous=true)
        assertEquals("Create a standalone Item",interpretationOperationLabel(plain.operation))
        assertEquals("High confidence",interpretationConfidenceLabel(plain))
        assertEquals("Confirmation required",interpretationConfidenceLabel(completion))
        assertEquals("Needs correction",interpretationConfidenceLabel(ambiguous))
        assertEquals("confirm",ManualCapture("Complete milk",interpretationAction="confirm").interpretationAction)
    }
}
