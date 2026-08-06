package com.shopagent.ui.components

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.draw.scale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.shopagent.domain.Sku
import java.util.Locale

fun formatPrice(price: Double): String = "¥" + String.format(Locale.US, "%.2f", price)

private fun Sku.propertySummary(): String =
    properties.entries.joinToString(" · ") { "${it.key}: ${it.value}" }

/**
 * SKU 堆叠组件：
 * - 单 SKU 退化为普通单卡
 * - 多 SKU 折叠态以 offset + 旋转 + 缩放形成堆叠感，右上角角标显示数量
 * - 点击整叠展开为竖向列表，再次点击收起
 */
@Composable
fun SkuStack(
    skus: List<Sku>,
    modifier: Modifier = Modifier,
) {
    if (skus.isEmpty()) return
    if (skus.size == 1) {
        SkuCard(sku = skus.first(), modifier = modifier)
        return
    }

    var expanded by remember { mutableStateOf(false) }

    Column(modifier = modifier) {
        if (!expanded) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { expanded = true },
            ) {
                // 下层卡先画，顶层最后画
                skus.asReversed().forEachIndexed { reverseIndex, sku ->
                    val index = skus.size - 1 - reverseIndex
                    if (index == 0) {
                        // 顶层卡完整露出
                        SkuCard(sku = sku, modifier = Modifier.fillMaxWidth())
                    } else {
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .offset(y = (index * 6).dp)
                                .rotate(if (index % 2 == 0) 2f else -2f)
                                .scale(1f - index * 0.03f)
                                .clip(RoundedCornerShape(8.dp))
                                .background(MaterialTheme.colorScheme.surfaceVariant)
                                .height(52.dp),
                        )
                    }
                }
                // 数量角标
                Surface(
                    color = MaterialTheme.colorScheme.primary,
                    shape = RoundedCornerShape(10.dp),
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(4.dp),
                ) {
                    Text(
                        text = "${skus.size} 个 SKU",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onPrimary,
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                    )
                }
            }
        }

        AnimatedVisibility(visible = expanded) {
            Column(
                verticalArrangement = Arrangement.spacedBy(6.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { expanded = false },
            ) {
                skus.forEach { sku ->
                    SkuCard(sku = sku, expanded = true, modifier = Modifier.fillMaxWidth())
                }
            }
        }
    }
}

@Composable
private fun SkuCard(
    sku: Sku,
    modifier: Modifier = Modifier,
    expanded: Boolean = false,
) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(8.dp),
        modifier = modifier,
    ) {
        Row(
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 10.dp, vertical = 8.dp),
        ) {
            Text(
                text = sku.propertySummary().ifBlank { sku.skuId },
                style = MaterialTheme.typography.bodySmall,
                maxLines = if (expanded) Int.MAX_VALUE else 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Text(
                text = formatPrice(sku.price),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(start = 8.dp),
            )
        }
    }
}
