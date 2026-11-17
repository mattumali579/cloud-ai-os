package com.mattumali.visortracker

/**
 * Turns MediaPipe category scores into a name that is safe to draw.
 *
 * EfficientDet-Lite0's COCO labels have no "glasses" class. On this camera it
 * can call a phone a laptop and eyewear a pair of scissors. A close score, or
 * a confusion-prone name below its own floor, is shown as [UNCERTAIN] instead
 * of a confident wrong noun. Names below [MIN_SCORE] are omitted entirely.
 * This does not invent a correct class the model never predicted.
 */
data class CategoryScore(val name: String, val score: Float)

object LabelPolicy {
    const val MIN_SCORE = 0.45f
    const val UNCERTAIN = "object"
    private const val AMBIGUITY_MARGIN = 0.15f

    private val strictFloor = mapOf(
        "scissors" to 0.72f,
        "laptop" to 0.62f,
        "cell phone" to 0.55f,
        "knife" to 0.62f,
    )

    private val confusableWith = mapOf(
        "cell phone" to setOf("laptop", "remote", "book"),
        "laptop" to setOf("cell phone", "book", "tv", "keyboard"),
        "scissors" to setOf("knife", "fork", "toothbrush", "cell phone"),
        "knife" to setOf("scissors", "fork"),
        "book" to setOf("laptop", "cell phone"),
    )

    fun resolve(categories: List<CategoryScore>): String? {
        val ranked = categories
            .asSequence()
            .filter { it.name.isNotBlank() && it.score.isFinite() && it.score >= MIN_SCORE }
            .sortedByDescending { it.score }
            .toList()
        val top = ranked.firstOrNull() ?: return null
        if (top.score < (strictFloor[top.name] ?: MIN_SCORE)) return UNCERTAIN
        val rival = ranked.getOrNull(1)
        if (rival != null && top.score - rival.score < AMBIGUITY_MARGIN) {
            val partners = confusableWith[top.name]
            if (partners != null && rival.name in partners) return UNCERTAIN
        }
        return top.name
    }
}
