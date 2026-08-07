package com.shopagent

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.room.Room
import com.shopagent.data.ChatApi
import com.shopagent.data.ChatRepository
import com.shopagent.data.local.AppDatabase
import com.shopagent.data.local.ConversationStore
import com.shopagent.data.local.RoomConversationStore
import com.shopagent.ui.AppDrawer
import com.shopagent.ui.AppTab
import com.shopagent.ui.ChatScreen
import com.shopagent.ui.ChatViewModel
import com.shopagent.ui.cart.CartScreen
import com.shopagent.ui.profile.ProfileScreen
import com.shopagent.ui.theme.ShopAgentTheme
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    // 手动依赖注入：单 Repository + Room 会话存储 + ViewModelFactory，不引入 Hilt
    private val chatRepository by lazy { ChatRepository(ChatApi(BuildConfig.API_BASE_URL)) }
    private val database by lazy {
        Room.databaseBuilder(applicationContext, AppDatabase::class.java, "shopagent.db").build()
    }
    private val conversationStore by lazy { RoomConversationStore(database) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            ShopAgentTheme {
                AppRoot(chatRepository, conversationStore)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AppRoot(
    chatRepository: ChatRepository,
    conversationStore: ConversationStore,
) {
    var selectedTab by remember { mutableIntStateOf(AppTab.Chat.ordinal) }
    val drawerState = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val chatViewModel: ChatViewModel = viewModel(
        factory = ChatViewModel.Factory(chatRepository, conversationStore),
    )
    val conversations by chatViewModel.conversations.collectAsStateWithLifecycle()
    val currentConversationId by chatViewModel.conversationId.collectAsStateWithLifecycle()
    val currentTab = AppTab.entries[selectedTab]

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            AppDrawer(
                selectedTab = currentTab,
                onSelect = { tab ->
                    selectedTab = tab.ordinal
                    scope.launch { drawerState.close() }
                },
                onNewChat = {
                    chatViewModel.newConversation()
                    selectedTab = AppTab.Chat.ordinal
                    scope.launch { drawerState.close() }
                },
                conversations = conversations,
                currentConversationId = currentConversationId,
                onOpenConversation = { conversationId ->
                    chatViewModel.openConversation(conversationId)
                    selectedTab = AppTab.Chat.ordinal
                    scope.launch { drawerState.close() }
                },
            )
        },
    ) {
        Scaffold(
            topBar = {
                // 仿豆包：顶部不随 Tab 变化，只展示居中的小字产品名
                CenterAlignedTopAppBar(
                    title = {
                        Text(
                            text = "导购助手",
                            style = MaterialTheme.typography.titleMedium,
                        )
                    },
                    navigationIcon = {
                        IconButton(onClick = { scope.launch { drawerState.open() } }) {
                            Icon(Icons.Default.Menu, contentDescription = "打开导航菜单")
                        }
                    },
                )
            },
        ) { innerPadding ->
            val modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                // 消费掉 innerPadding，避免 ChatScreen 的 imePadding 与其叠加产生缝隙
                .consumeWindowInsets(innerPadding)
            when (currentTab) {
                AppTab.Chat -> ChatScreen(viewModel = chatViewModel, modifier = modifier)
                AppTab.Cart -> CartScreen(modifier = modifier)
                AppTab.Profile -> ProfileScreen(modifier = modifier)
            }
        }
    }
}
