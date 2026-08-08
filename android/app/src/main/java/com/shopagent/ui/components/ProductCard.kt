package com.shopagent.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.imageLoader
import coil.request.ImageRequest
import com.shopagent.domain.ProductCard as ProductCardModel

/** 商品卡片横向列表，按 rank 排序；点击卡片弹出商品详情 BottomSheet */
@Composable
fun ProductCardRow(
    products: List<ProductCardModel>,
    modifier: Modifier = Modifier,
) {
    var selectedProduct by remember { mutableStateOf<ProductCardModel?>(null) }

    // 预热：提前把卡片图拉进 Coil 内存/磁盘缓存，减少横滑与详情弹窗的占位闪烁
    val context = LocalContext.current
    LaunchedEffect(products) {
        products.forEach { product ->
            product.imageUrl?.let { url ->
                context.imageLoader.enqueue(
                    ImageRequest.Builder(context).data(url).build(),
                )
            }
        }
    }

    LazyRow(
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        contentPadding = PaddingValues(horizontal = 4.dp, vertical = 4.dp),
        modifier = modifier.fillMaxWidth(),
    ) {
        items(
            items = products.sortedBy { it.rank },
            key = { it.productId },
        ) { product ->
            ProductCard(
                product = product,
                onClick = { selectedProduct = product },
            )
        }
    }

    selectedProduct?.let { product ->
        ProductDetailSheet(
            product = product,
            onDismiss = { selectedProduct = null },
        )
    }
}

@Composable
fun ProductCard(
    product: ProductCardModel,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Card(
        modifier = modifier
            .width(200.dp)
            .clickable(onClick = onClick),
    ) {
        Column {
            // 占位图衬底 + AsyncImage 覆盖：缓存命中时首帧直接出图，
            // 避免 SubcomposeAsyncImage 子组合导致的占位闪烁
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(1f),
            ) {
                ImagePlaceholder()
                AsyncImage(
                    model = product.imageUrl,
                    contentDescription = product.title,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize(),
                )
            }
            Column(modifier = Modifier.padding(10.dp)) {
                Text(
                    text = product.title,
                    style = MaterialTheme.typography.titleSmall,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    text = product.brand,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    text = formatPrice(product.displayPrice),
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.padding(top = 4.dp),
                )
                if (product.basePrice != product.displayPrice) {
                    Text(
                        text = formatPrice(product.basePrice),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        textDecoration = TextDecoration.LineThrough,
                    )
                }
                if (product.matchedSkus.isNotEmpty()) {
                    Text(
                        text = "共 ${product.matchedSkus.size} 个 SKU，点击查看",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 8.dp),
                    )
                }
            }
        }
    }
}

/** image_url 为 null 或加载失败时的占位图 */
@Composable
internal fun ImagePlaceholder() {
    Box(
        contentAlignment = Alignment.Center,
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Text(
            text = "暂无图片",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
