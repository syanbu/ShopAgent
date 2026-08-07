package com.shopagent.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shopagent.domain.ChatMessage
import com.shopagent.ui.components.FloatingInputBar
import com.shopagent.ui.components.MessageBubble

@Composable
fun ChatScreen(
    viewModel: ChatViewModel,
    modifier: Modifier = Modifier,
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val listState = rememberLazyListState()

    // reverseLayout 下 index 0 即最新消息：新内容到达时回到底部
    LaunchedEffect(uiState.messages.lastOrNull()?.let { it.id to (it as? ChatMessage.Assistant)?.text?.length }) {
        if (uiState.messages.isNotEmpty()) {
            listState.animateScrollToItem(0)
        }
    }

    // imePadding 作用在整个 Box 上：键盘弹出时列表与输入栏一起上抬。
    // 配合 reverseLayout 的底部锚定，视口收缩时最新消息自然保持在输入栏上方
    Box(modifier = modifier.fillMaxSize().imePadding()) {
        LazyColumn(
            state = listState,
            reverseLayout = true,
            // 底部预留悬浮输入栏高度，最后一条消息不被遮挡
            contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 12.dp, bottom = 88.dp),
            modifier = Modifier.fillMaxSize(),
        ) {
            items(
                items = uiState.messages.asReversed(),
                key = { it.id },
                contentType = {
                    when (it) {
                        is ChatMessage.User -> "user"
                        is ChatMessage.Assistant -> "assistant"
                    }
                },
            ) { message ->
                MessageBubble(
                    message = message,
                    onRetry = viewModel::retry,
                    modifier = Modifier.padding(vertical = 4.dp),
                )
            }
        }

        FloatingInputBar(
            isStreaming = uiState.isStreaming,
            onSend = viewModel::send,
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth(),
        )
    }
}
