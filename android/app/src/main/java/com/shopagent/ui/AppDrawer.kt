package com.shopagent.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Email
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.NavigationDrawerItemDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.shopagent.data.local.ConversationSummary
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** 顶层页面。抽屉导航的切换目标。 */
enum class AppTab(val label: String, val icon: ImageVector) {
    Chat("聊天", Icons.Default.Email),
    Cart("购物车", Icons.Default.ShoppingCart),
    Profile("我的", Icons.Default.Person),
}

/**
 * 侧边抽屉：功能项 / 历史会话列表 / 底部用户行。
 * 历史会话来自本地 Room 持久化，点击载入并可继续聊；用户行为纯展示占位。
 */
@Composable
fun AppDrawer(
    selectedTab: AppTab,
    onSelect: (AppTab) -> Unit,
    onNewChat: () -> Unit,
    conversations: List<ConversationSummary>,
    currentConversationId: String?,
    onOpenConversation: (String) -> Unit,
) {
    ModalDrawerSheet {
        Column(modifier = Modifier.fillMaxWidth()) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    text = "ShopAgent",
                    style = MaterialTheme.typography.titleLarge,
                    modifier = Modifier.padding(horizontal = 28.dp, vertical = 16.dp),
                )
                Spacer(modifier = Modifier.weight(1f))
                // 新会话：清空当前对话，后端将分配新的 conversation_id
                IconButton(
                    onClick = onNewChat,
                    modifier = Modifier.padding(end = 16.dp),
                ) {
                    Icon(Icons.Default.Add, contentDescription = "新会话")
                }
            }

            AppTab.entries.forEach { tab ->
                NavigationDrawerItem(
                    label = { Text(tab.label) },
                    icon = { Icon(tab.icon, contentDescription = tab.label) },
                    selected = tab == selectedTab,
                    onClick = { onSelect(tab) },
                    modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding),
                )
            }

            HorizontalDivider(modifier = Modifier.padding(vertical = 12.dp))
            Text(
                text = "历史会话",
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 28.dp),
            )
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f),
            ) {
                if (conversations.isEmpty()) {
                    Text(
                        text = "暂无历史会话",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.outline,
                        modifier = Modifier.align(Alignment.Center),
                    )
                } else {
                    LazyColumn(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(top = 4.dp),
                    ) {
                        items(items = conversations, key = { it.id }) { conversation ->
                            ConversationRow(
                                summary = conversation,
                                selected = conversation.id == currentConversationId,
                                onClick = { onOpenConversation(conversation.id) },
                            )
                        }
                    }
                }
            }

            HorizontalDivider()
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 28.dp, vertical = 16.dp),
            ) {
                Surface(
                    shape = CircleShape,
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    modifier = Modifier.size(36.dp),
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        Icon(
                            Icons.Default.Person,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                Text(
                    text = "未登录用户",
                    style = MaterialTheme.typography.bodyLarge,
                    modifier = Modifier.padding(start = 12.dp),
                )
            }
        }
    }
}

@Composable
private fun ConversationRow(
    summary: ConversationSummary,
    selected: Boolean,
    onClick: () -> Unit,
) {
    val timeFormat = remember { SimpleDateFormat("MM-dd HH:mm", Locale.getDefault()) }
    Surface(
        onClick = onClick,
        shape = RoundedCornerShape(12.dp),
        color = if (selected) MaterialTheme.colorScheme.secondaryContainer else Color.Transparent,
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 2.dp),
    ) {
        Column(modifier = Modifier.padding(horizontal = 16.dp, vertical = 10.dp)) {
            Text(
                text = summary.title,
                style = MaterialTheme.typography.bodyLarge,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                text = timeFormat.format(Date(summary.updatedAt)),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
