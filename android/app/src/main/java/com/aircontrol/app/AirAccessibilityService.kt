package com.aircontrol.app

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Context
import android.content.Intent
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Ejecuta en todo el teléfono lo que decide el motor: toques, pulsación larga, arrastre, deslizamiento, Atrás/Inicio/Recientes
 * y escribir texto, y dibuja un cursor encima de todo (ventana de accesibilidad: no necesita el permiso de superponer).
 */
class AirAccessibilityService : AccessibilityService() {
    companion object {
        @Volatile var instance: AirAccessibilityService? = null
    }

    private val main = Handler(Looper.getMainLooper())
    private var windowManager: WindowManager? = null
    private var cursor: CursorView? = null
    private var cursorParams: WindowManager.LayoutParams? = null
    private var cursorSize = 0

    // Estado del arrastre: Android pide el trazo entero al enviar un gesto, así que se encadenan trazos que "continúan".
    private var dragStroke: GestureDescription.StrokeDescription? = null
    private var dragActive = false
    private var dragBusy = false
    private var dragEnding = false
    private var dragX = 0
    private var dragY = 0
    private var targetX = 0
    private var targetY = 0

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        PhoneBridge.initScreen(this)
        main.post { addCursor() }
    }

    override fun onUnbind(intent: Intent?): Boolean {
        instance = null
        main.post { removeCursor() }
        return super.onUnbind(intent)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}

    // --- cursor ----------------------------------------------------------------------------------------------------
    private fun addCursor() {
        if (cursor != null) return
        val wm = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        cursorSize = (32 * resources.displayMetrics.density).toInt()
        val params = WindowManager.LayoutParams(
            cursorSize, cursorSize, WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        )
        params.gravity = Gravity.TOP or Gravity.START
        params.x = PhoneBridge.screenW / 2
        params.y = PhoneBridge.screenH / 2
        val view = CursorView(this)
        wm.addView(view, params)
        windowManager = wm; cursor = view; cursorParams = params
    }

    private fun removeCursor() {
        try { cursor?.let { windowManager?.removeView(it) } } catch (_: Exception) {}
        cursor = null
    }

    fun moveCursor(x: Int, y: Int) {
        main.post {
            val p = cursorParams ?: return@post
            val v = cursor ?: return@post
            p.x = x - cursorSize / 2
            p.y = y - cursorSize / 2
            v.dragging = dragActive
            try { windowManager?.updateViewLayout(v, p) } catch (_: Exception) {}
        }
    }

    private class CursorView(context: Context) : View(context) {
        var dragging = false                       // (no se llama `pressed`: chocaría con View.setPressed)
        private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
        private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 4f; color = 0xFFFFFFFF.toInt() }
        override fun onDraw(canvas: Canvas) {
            fill.color = if (dragging) 0xCCFF8C00.toInt() else 0x8800C896.toInt()
            val c = width / 2f
            canvas.drawCircle(c, c, c - 4f, fill)
            canvas.drawCircle(c, c, c - 4f, ring)
        }
    }

    // --- gestos ----------------------------------------------------------------------------------------------------
    private fun clampX(x: Int) = x.coerceIn(1, PhoneBridge.screenW - 2)
    private fun clampY(y: Int) = y.coerceIn(1, PhoneBridge.screenH - 2)
    private fun pointPath(x: Int, y: Int) = Path().apply { moveTo(clampX(x).toFloat(), clampY(y).toFloat()) }

    fun tap(x: Int, y: Int, count: Int) {
        main.post {
            val builder = GestureDescription.Builder()
            for (i in 0 until count.coerceIn(1, 3)) {
                builder.addStroke(GestureDescription.StrokeDescription(pointPath(x, y), i * 130L, 50L))
            }
            dispatchGesture(builder.build(), null, null)
        }
    }

    fun longPress(x: Int, y: Int) {
        main.post {
            val stroke = GestureDescription.StrokeDescription(pointPath(x, y), 0L, 650L)
            dispatchGesture(GestureDescription.Builder().addStroke(stroke).build(), null, null)
        }
    }

    /** Desliza el dedo de (x, y) a (x+dx, y+dy): el contenido sigue al dedo (dy > 0 baja el contenido). */
    fun swipe(x: Int, y: Int, dx: Int, dy: Int) {
        main.post {
            val path = Path().apply {
                moveTo(clampX(x).toFloat(), clampY(y).toFloat())
                lineTo(clampX(x + dx).toFloat(), clampY(y + dy).toFloat())
            }
            val stroke = GestureDescription.StrokeDescription(path, 0L, 200L)
            dispatchGesture(GestureDescription.Builder().addStroke(stroke).build(), null, null)
        }
    }

    fun dragStart(x: Int, y: Int) {
        main.post {
            dragActive = true; dragEnding = false; dragBusy = false
            dragX = clampX(x); dragY = clampY(y); targetX = dragX; targetY = dragY
            val stroke = GestureDescription.StrokeDescription(pointPath(dragX, dragY), 0L, 20L, true)
            dragStroke = stroke
            sendDrag(stroke)
        }
    }

    fun dragMove(x: Int, y: Int) {
        main.post { targetX = clampX(x); targetY = clampY(y); pumpDrag() }
    }

    fun dragEnd(x: Int, y: Int) {
        main.post { targetX = clampX(x); targetY = clampY(y); dragEnding = true; pumpDrag() }
    }

    private fun sendDrag(stroke: GestureDescription.StrokeDescription) {
        dragBusy = true
        val ok = dispatchGesture(GestureDescription.Builder().addStroke(stroke).build(), object : GestureResultCallback() {
            override fun onCompleted(gestureDescription: GestureDescription?) { main.post { dragBusy = false; if (dragEnding && dragStroke == null) dragActive = false; pumpDrag() } }
            override fun onCancelled(gestureDescription: GestureDescription?) { main.post { resetDrag() } }
        }, null)
        if (!ok) resetDrag()
    }

    /** Si hay algo pendiente (un movimiento o el final) y no hay un trazo en curso, envía el siguiente. */
    private fun pumpDrag() {
        val last = dragStroke
        if (!dragActive || dragBusy || last == null) return
        if (dragEnding) {
            val path = Path().apply { moveTo(dragX.toFloat(), dragY.toFloat()); lineTo(targetX.toFloat(), targetY.toFloat()) }
            dragStroke = null                                   // trazo final: willContinue = false
            dragX = targetX; dragY = targetY
            sendDrag(last.continueStroke(path, 0L, 30L, false))
        } else if (targetX != dragX || targetY != dragY) {
            val path = Path().apply { moveTo(dragX.toFloat(), dragY.toFloat()); lineTo(targetX.toFloat(), targetY.toFloat()) }
            dragX = targetX; dragY = targetY
            val next = last.continueStroke(path, 0L, 40L, true)
            dragStroke = next
            sendDrag(next)
        }
    }

    private fun resetDrag() {
        dragActive = false; dragBusy = false; dragEnding = false; dragStroke = null
    }

    // --- navegación y texto ----------------------------------------------------------------------------------------
    fun nav(name: String) {
        val action = when (name) {
            "back" -> GLOBAL_ACTION_BACK
            "home" -> GLOBAL_ACTION_HOME
            "recents" -> GLOBAL_ACTION_RECENTS
            "notifications" -> GLOBAL_ACTION_NOTIFICATIONS
            "quick_settings" -> GLOBAL_ACTION_QUICK_SETTINGS
            else -> return
        }
        main.post { performGlobalAction(action) }
    }

    /** Añade texto al campo que tiene el foco (si lo hay). */
    fun typeText(text: String) {
        main.post {
            val node = findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: return@post
            val current = node.text?.toString() ?: ""
            val args = Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, current + text)
            }
            node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        }
    }
}
