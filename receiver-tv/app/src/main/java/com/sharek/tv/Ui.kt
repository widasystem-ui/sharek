package com.sharek.tv

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.StateListDrawable
import android.view.Gravity
import android.view.View
import android.widget.TextView

/** Sharek TV palette and small view helpers (no AndroidX, programmatic views). */
object Ui {
    val BG_TOP = Color.parseColor("#0C1E3C")
    val BG_BOTTOM = Color.parseColor("#040A16")
    val CARD = Color.parseColor("#13284B")
    val CARD_STROKE = Color.parseColor("#26FFFFFF")
    val MINT = Color.parseColor("#4FD1A5")
    val MINT_DARK = Color.parseColor("#2FB287")
    val INK = Color.parseColor("#06101F")
    val TEXT = Color.WHITE
    val MUTED = Color.parseColor("#9DB3D1")
    val DIM = Color.parseColor("#6F86A8")
    val DANGER = Color.parseColor("#FF7A7A")
    val SCRIM = Color.parseColor("#E6040A16")

    fun dp(c: Context, v: Float) = (v * c.resources.displayMetrics.density)
    fun dpi(c: Context, v: Float) = dp(c, v).toInt()

    fun background(): GradientDrawable =
        GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM, intArrayOf(BG_TOP, BG_BOTTOM))

    fun rounded(c: Context, color: Int, radiusDp: Float, strokeColor: Int = 0, strokeDp: Float = 0f) =
        GradientDrawable().apply {
            setColor(color)
            cornerRadius = dp(c, radiusDp)
            if (strokeDp > 0) setStroke(dpi(c, strokeDp), strokeColor)
        }

    fun text(c: Context, s: String, sp: Float, color: Int, bold: Boolean = false) = TextView(c).apply {
        text = s
        textSize = sp
        setTextColor(color)
        gravity = Gravity.CENTER
        if (bold) typeface = Typeface.DEFAULT_BOLD
        includeFontPadding = true
    }

    enum class Kind { PRIMARY, SECONDARY, DANGER }

    /** A remote-friendly button: clear focus ring + slight zoom when selected with the D-pad. */
    fun button(c: Context, label: String, kind: Kind, onClick: () -> Unit): TextView {
        val (fill, textColor, stroke) = when (kind) {
            Kind.PRIMARY -> Triple(MINT, INK, MINT)
            Kind.SECONDARY -> Triple(Color.parseColor("#1AFFFFFF"), TEXT, Color.parseColor("#33FFFFFF"))
            Kind.DANGER -> Triple(Color.parseColor("#14FF7A7A"), DANGER, Color.parseColor("#55FF7A7A"))
        }
        val normal = rounded(c, fill, 18f, stroke, 1.5f)
        val focused = rounded(c, fill, 18f, Color.WHITE, 3f)
        val bg = StateListDrawable().apply {
            addState(intArrayOf(android.R.attr.state_focused), focused)
            addState(intArrayOf(), normal)
        }
        return TextView(c).apply {
            text = label
            textSize = 20f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(textColor)
            gravity = Gravity.CENTER
            background = bg
            isFocusable = true
            isClickable = true
            setPadding(dpi(c, 34f), dpi(c, 14f), dpi(c, 34f), dpi(c, 14f))
            minWidth = dpi(c, 150f)
            setOnClickListener { onClick() }
            setOnFocusChangeListener { v, has ->
                v.animate().scaleX(if (has) 1.07f else 1f).scaleY(if (has) 1.07f else 1f).setDuration(140).start()
            }
        }
    }
}

/** Sharek mark: mint rounded tile with a screen and broadcast arcs. */
class LogoView(c: Context) : View(c) {
    private val tile = Paint(Paint.ANTI_ALIAS_FLAG)
    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
        color = Ui.INK
    }

    override fun onDraw(canvas: Canvas) {
        val w = width.toFloat()
        val h = height.toFloat()
        val s = minOf(w, h)
        tile.shader = LinearGradient(0f, 0f, w, h, Color.parseColor("#6BE3BC"), Ui.MINT_DARK, Shader.TileMode.CLAMP)
        canvas.drawRoundRect(RectF(0f, 0f, w, h), s * 0.26f, s * 0.26f, tile)
        line.strokeWidth = s * 0.06f
        val cx = w / 2
        val sw = s * 0.56f
        val sh = s * 0.36f
        val top = h * 0.30f
        canvas.drawRoundRect(RectF(cx - sw / 2, top, cx + sw / 2, top + sh), s * 0.05f, s * 0.05f, line)
        canvas.drawLine(cx - sw * 0.22f, top + sh + s * 0.11f, cx + sw * 0.22f, top + sh + s * 0.11f, line)
        val cy = top + sh * 0.78f
        for (i in 1..2) {
            val r = s * 0.075f * i
            canvas.drawArc(RectF(cx - r, cy - r, cx + r, cy + r), 225f, 90f, false, line)
        }
        canvas.drawCircle(cx, cy, s * 0.03f, Paint(line).apply { style = Paint.Style.FILL })
    }
}
