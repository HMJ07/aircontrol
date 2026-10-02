package com.aircontrol.app

import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.net.Uri
import android.os.Build
import android.util.DisplayMetrics
import android.view.KeyEvent
import android.view.WindowManager

/**
 * Lo que el motor (Python) puede hacer en el teléfono. Python recibe este objeto y llama a estos métodos desde cualquier hilo;
 * los que tocan la pantalla se delegan al Servicio de Accesibilidad.
 */
object PhoneBridge {
    @Volatile var appContext: Context? = null
    @Volatile var screenW: Int = 1080
    @Volatile var screenH: Int = 2400

    private val service get() = AirAccessibilityService.instance

    /** Tamaño real de la pantalla en píxeles (las coordenadas del cursor y de los gestos están en esos píxeles). */
    fun initScreen(context: Context) {
        appContext = context.applicationContext
        val wm = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        if (Build.VERSION.SDK_INT >= 30) {
            val b = wm.currentWindowMetrics.bounds
            screenW = b.width(); screenH = b.height()
        } else {
            val m = DisplayMetrics()
            @Suppress("DEPRECATION") wm.defaultDisplay.getRealMetrics(m)
            screenW = m.widthPixels; screenH = m.heightPixels
        }
    }

    fun move(x: Int, y: Int) { service?.moveCursor(x, y) }
    fun tap(x: Int, y: Int, count: Int) { service?.tap(x, y, count) }
    fun longPress(x: Int, y: Int) { service?.longPress(x, y) }
    fun dragStart(x: Int, y: Int) { service?.dragStart(x, y) }
    fun dragMove(x: Int, y: Int) { service?.dragMove(x, y) }
    fun dragEnd(x: Int, y: Int) { service?.dragEnd(x, y) }
    fun swipe(x: Int, y: Int, dx: Int, dy: Int) { service?.swipe(x, y, dx, dy) }
    fun nav(name: String) { service?.nav(name) }
    fun typeText(text: String) { service?.typeText(text) }

    fun media(name: String) {
        val ctx = appContext ?: return
        val audio = ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        when (name) {
            "volume_up" -> audio.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_RAISE, AudioManager.FLAG_SHOW_UI)
            "volume_down" -> audio.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_LOWER, AudioManager.FLAG_SHOW_UI)
            "mute" -> audio.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_TOGGLE_MUTE, AudioManager.FLAG_SHOW_UI)
            else -> {
                val code = when (name) {
                    "play_pause" -> KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE
                    "next" -> KeyEvent.KEYCODE_MEDIA_NEXT
                    "prev" -> KeyEvent.KEYCODE_MEDIA_PREVIOUS
                    else -> return
                }
                audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, code))
                audio.dispatchMediaKeyEvent(KeyEvent(KeyEvent.ACTION_UP, code))
            }
        }
    }

    /** Solo direcciones web: abrir una app por su nombre exigiría el permiso QUERY_ALL_PACKAGES. */
    fun openTarget(target: String) {
        val ctx = appContext ?: return
        if (!target.contains("://")) return
        ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(target)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}
