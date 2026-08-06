package com.shopagent

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.material.icons.filled.Email
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import com.shopagent.data.ChatApi
import com.shopagent.data.ChatRepository
import com.shopagent.ui.ChatScreen
import com.shopagent.ui.ChatViewModel
import com.shopagent.ui.cart.CartScreen
import com.shopagent.ui.profile.ProfileScreen
import com.shopagent.ui.theme.ShopAgentTheme

class MainActivity : ComponentActivity() {

    // 手动依赖注入：单 Repository + ViewModelFactory，不引入 Hilt
    private val chatRepository by lazy { ChatRepository(ChatApi(BuildConfig.API_BASE_URL)) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            ShopAgentTheme {
                AppRoot(chatRepository)
            }
        }
    }
}

private enum class Tab(val label: String) {
    Chat("聊天"),
    Cart("购物车"),
    Profile("我的"),
}

@Composable
private fun AppRoot(chatRepository: ChatRepository) {
    var selectedTab by remember { mutableIntStateOf(Tab.Chat.ordinal) }
    val chatViewModel: ChatViewModel = viewModel(
        factory = ChatViewModel.Factory(chatRepository),
    )

    Scaffold(
        bottomBar = {
            NavigationBar {
                Tab.entries.forEach { tab ->
                    NavigationBarItem(
                        selected = selectedTab == tab.ordinal,
                        onClick = { selectedTab = tab.ordinal },
                        icon = {
                            when (tab) {
                                Tab.Chat -> Icon(Icons.Default.Email, contentDescription = tab.label)
                                Tab.Cart -> Icon(Icons.Default.ShoppingCart, contentDescription = tab.label)
                                Tab.Profile -> Icon(Icons.Default.Person, contentDescription = tab.label)
                            }
                        },
                        label = { Text(tab.label) },
                    )
                }
            }
        },
    ) { innerPadding ->
        val modifier = Modifier
            .fillMaxSize()
            .padding(innerPadding)
            // 消费掉 innerPadding，避免 ChatScreen 的 imePadding 与底部导航栏高度叠加产生缝隙
            .consumeWindowInsets(innerPadding)
        when (Tab.entries[selectedTab]) {
            Tab.Chat -> ChatScreen(viewModel = chatViewModel, modifier = modifier)
            Tab.Cart -> CartScreen(modifier = modifier)
            Tab.Profile -> ProfileScreen(modifier = modifier)
        }
    }
}
