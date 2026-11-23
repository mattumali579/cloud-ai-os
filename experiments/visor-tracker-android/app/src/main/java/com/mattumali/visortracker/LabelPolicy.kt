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
    const val MIN_SCORE = 0.25f
    const val UNCERTAIN = "object"
    private const val AMBIGUITY_MARGIN = 0.15f

    fun minScoreFor(category: String): Float = when (category.lowercase()) {
        "suitcase", "backpack", "handbag", "book", "clock", "bottle", "airplane",
        "clothes hamper", "laundry basket", "hanging clothes", "closet clothing", "wardrobe clothes",
        "portable air conditioner", "air conditioner", "covered object", "white plastic trash bag", "white plastic-covered item" -> 0.12f
        else -> MIN_SCORE
    }

    private val strictFloor = mapOf(
        "scissors" to 0.72f,
        "laptop" to 0.62f,
        "cell phone" to 0.55f,
        "knife" to 0.62f,
        "book" to 0.25f,
        "backpack" to 0.25f,
        "clock" to 0.25f,
        "bottle" to 0.25f,
        "airplane" to 0.25f,
        "suitcase" to 0.12f,
        "handbag" to 0.12f,
        "clothes hamper" to 0.15f,
        "laundry basket" to 0.15f,
        "hanging clothes" to 0.15f,
        "closet clothing" to 0.15f,
        "wardrobe clothes" to 0.15f,
        "portable air conditioner" to 0.15f,
        "air conditioner" to 0.15f,
        "covered object" to 0.15f,
        "white plastic trash bag" to 0.15f,
        "white plastic-covered item" to 0.15f,
    )

    private val confusableWith = mapOf(
        "cell phone" to setOf("laptop", "remote", "book"),
        "laptop" to setOf("cell phone", "book", "tv", "keyboard"),
        "scissors" to setOf("knife", "fork", "toothbrush", "cell phone"),
        "knife" to setOf("scissors", "fork"),
        "book" to setOf("laptop", "cell phone"),
    )

    /**
     * The name to draw and the finite score of the category that earned it.
     * A blank or non-finite score cannot outrank a real category, and a score
     * under [MIN_SCORE] (or [minScoreFor]) is omitted. Returning null is the only drop.
     */
    fun selection(categories: List<CategoryScore>): CategoryScore? {
        val ranked = categories
            .asSequence()
            .filter { it.name.isNotBlank() && it.score.isFinite() && it.score >= minScoreFor(it.name) }
            .sortedByDescending { it.score }
            .toList()
        val top = ranked.firstOrNull() ?: return null
        // Keep "person" so HandPriority can drop a wearer arm. Rewriting it to
        // "object" made 10917 lock the forearm as a table item.
        if (top.name.equals("person", ignoreCase = true)) return top
        if (top.score < (strictFloor[top.name] ?: 0.45f)) return CategoryScore(UNCERTAIN, top.score)
        val rival = ranked.getOrNull(1)
        if (rival != null && top.score - rival.score < AMBIGUITY_MARGIN) {
            val partners = confusableWith[top.name]
            if (partners != null && rival.name in partners) return CategoryScore(UNCERTAIN, top.score)
        }
        return top
    }

    fun resolve(categories: List<CategoryScore>): String? = selection(categories)?.name
}
