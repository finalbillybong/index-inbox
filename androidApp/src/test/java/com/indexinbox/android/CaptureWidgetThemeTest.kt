package com.indexinbox.android

import android.content.res.Configuration
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CaptureWidgetThemeTest {
    @Test
    fun explicitDarkOverridesLightOs() {
        assertTrue(widgetUsesDarkTheme("dark", Configuration.UI_MODE_NIGHT_NO))
    }

    @Test
    fun explicitLightOverridesDarkOs() {
        assertFalse(widgetUsesDarkTheme("light", Configuration.UI_MODE_NIGHT_YES))
    }

    @Test
    fun systemFollowsOsNightMode() {
        assertTrue(widgetUsesDarkTheme("system", Configuration.UI_MODE_NIGHT_YES))
        assertFalse(widgetUsesDarkTheme("system", Configuration.UI_MODE_NIGHT_NO))
    }
}
