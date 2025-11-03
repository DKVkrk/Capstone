#!/usr/bin/python3
"""
AI-Driven TE Agent using Classic Q-Learning (No TensorFlow) - FINAL + DASHBOARD

This agent uses a simple Q-Table (a dictionary) to learn the best path.

- STATE: A tuple representing traffic levels (e.g., 'low', 'high')
- ACTION: 0 (reroute via s2) or 1 (reroute via s3).
- REWARD: Negative of the max traffic (to minimize congestion).
"""
import requests
import time
import random
import numpy as np
import json
from collections import defaultdict

# --- Agent Configuration ---
ACTION_SIZE = 2     # Action 0: use Path A (port 2), Action 1: use Path B (port 3)
LEARNING_RATE = 0.1 # Alpha
GAMMA = 0.9         # Discount factor for future rewards
EPSILON_START = 1.0 # Exploration rate
EPSILON_END = 0.01
EPSILON_DECAY = 0.995

# --- Network Configuration ---
RYU_URL = "http://127.0.0.1:8080"
SWITCH_DPID = 1     # We are controlling Switch s1
PORT_A = 2          # Port 2 on s1 (to s2)
PORT_B = 3          # Port 3 on s1 (to s3)
HOST_IN_PORT = 1    # Port 1 on s1 (from h1)
HOST_DST_IP = "10.0.0.2" # Final destination is h2

class QLearningAgent:
    def __init__(self, action_size):
        self.action_size = action_size
        self.gamma = GAMMA
        self.epsilon = EPSILON_START
        self.learning_rate = LEARNING_RATE
        
        # This is the "brain". Instead of a TF model, it's a simple dictionary.
        self.q_table = defaultdict(lambda: np.zeros(self.action_size))

    def act(self, state):
        """
        Chooses an action using Epsilon-Greedy policy.
        """
        if np.random.rand() <= self.epsilon:
            return random.randrange(self.action_size) # Explore
        
        q_scores = self.q_table[state]
        return np.argmax(q_scores) # Exploit

    def learn(self, state, action, reward, next_state):
        """
        Updates the Q-Table using the Bellman equation.
        """
        current_q = self.q_table[state][action]
        next_max_q = np.max(self.q_table[next_state])
        
        # --- The Q-Learning Formula ---
        new_q = (1 - self.learning_rate) * current_q + \
                self.learning_rate * (reward + self.gamma * next_max_q)
        
        self.q_table[state][action] = new_q
        
        if self.epsilon > EPSILON_END:
            self.epsilon *= EPSILON_DECAY

def get_network_stats():
    """Fetches port stats from the Ryu API."""
    try:
        response = requests.get(f"{RYU_URL}/network_state")
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching stats: {e}")
        return None

def discretize_state(port_stats, last_port_tx):
    """
    Parses raw stats and *discretizes* them into a simple state tuple.
    """
    current_port_tx = {PORT_A: 0, PORT_B: 0}
    
    s1_stats = port_stats.get(str(SWITCH_DPID), [])
    
    for port in s1_stats:
        # --- THIS IS THE CRITICAL BUG FIX ---
        port_no = int(port.get('port_no', 0))
        # ------------------------------------
        
        tx_bytes = int(port.get('tx_bytes', 0))
        
        if port_no == PORT_A:
            current_port_tx[PORT_A] = tx_bytes
        elif port_no == PORT_B:
            current_port_tx[PORT_B] = tx_bytes

    throughput_A = current_port_tx[PORT_A] - last_port_tx[PORT_A]
    throughput_B = current_port_tx[PORT_B] - last_port_tx[PORT_B]
    
    if throughput_A < 0: throughput_A = 0
    if throughput_B < 0: throughput_B = 0

    def get_traffic_level(throughput):
        if throughput < 100000: # Less than 100KB
            return 'low'
        elif throughput < 1000000: # Less than 1MB
            return 'medium'
        else: # 1MB+
            return 'high'

    state = (get_traffic_level(throughput_A), get_traffic_level(throughput_B))
    raw_throughput = [throughput_A, throughput_B]
    
    return state, current_port_tx, raw_throughput

def calculate_reward(raw_throughput):
    """
    Calculates the reward: the negative of the max throughput.
    """
    return -1 * max(raw_throughput)

def execute_action(action):
    """
    Executes the chosen action (0 or 1) by sending a flow rule
    to the Ryu controller.
    """
    port_to_use = PORT_A if action == 0 else PORT_B
    
    print(f"  EXECUTING: Rerouting via Port {port_to_use}...")
    
    flow_rule = {
        "dpid": SWITCH_DPID,
        "priority": 100,
        "match": {
            "in_port": HOST_IN_PORT,
            "eth_type": 2048, # IPv4
            "ipv4_dst": HOST_DST_IP
        },
        "actions": [
            {
                "type": "OUTPUT",
                "port": port_to_use
            }
        ]
    }
    
    try:
        response = requests.post(f"{RYU_URL}/reroute_flow", json=flow_rule)
        response.raise_for_status()
        print(f"  SUCCESS: Flow rule for Port {port_to_use} added.")
    except Exception as e:
        print(f"  ERROR sending flow rule: {e}")

# --- NEW FUNCTION FOR DASHBOARD ---
def update_controller_q_table(agent):
    """Sends the agent's Q-Table to the Ryu controller for the dashboard."""
    
    # We must convert the defaultdict to a plain dict for JSON
    # And convert state tuples (('low', 'low')) to strings
    # And convert numpy arrays to simple lists
    q_table_serializable = {str(k): v.tolist() for k, v in agent.q_table.items()}
    
    try:
        requests.post(f"{RYU_URL}/update_q_table", json=q_table_serializable)
        # We don't need to print this every time
        # print("  Q-Table successfully sent to controller dashboard.")
    except Exception as e:
        print(f"  Error sending Q-Table to controller: {e}")

# --- Main Learning Loop ---
def main():
    agent = QLearningAgent(ACTION_SIZE)
    
    # Initialize last_port_tx to avoid a huge negative value on first run
    print("Initializing agent... fetching initial network state.")
    initial_stats = get_network_stats()
    if not initial_stats:
        print("Could not contact controller. Exiting.")
        return
        
    _, last_port_tx, _ = discretize_state(initial_stats, {PORT_A: 0, PORT_B: 0})
    print("Initialization complete. Starting learning loop.")

    for e in range(1, 1001):
        print(f"\n--- Episode {e} (Epsilon: {agent.epsilon:.3f}) ---")
        
        # 1. OBSERVE (State)
        port_stats = get_network_stats()
        if not port_stats:
            print("Could not get stats, sleeping...")
            time.sleep(5)
            continue
            
        state, current_port_tx, raw_throughput = discretize_state(port_stats, last_port_tx)
        
        # 2. DECIDE (Action)
        action = agent.act(state) # 0 or 1
        
        # 3. EXECUTE (Action)
        execute_action(action)
        
        # 4. GET FEEDBACK (New State & Reward)
        print("  Waiting for new state...")
        time.sleep(5)
        
        new_port_stats = get_network_stats()
        if not new_port_stats:
            continue
            
        next_state, next_port_tx, next_raw_throughput = discretize_state(new_port_stats, current_port_tx)
        
        # 5. CALCULATE REWARD
        reward = calculate_reward(next_raw_throughput)
        
        print(f"  State: {state}, Action: {action}, Reward: {reward}, Next State: {next_state}")
        
        # 6. LEARN
        agent.learn(state, action, reward, next_state)
        
        last_port_tx = next_port_tx
        
        # Print Q-Table and send to dashboard every 20 episodes
        if e % 20 == 0:
            print("--- Q-Table ---")
            for s, q in agent.q_table.items():
                print(f"  {s}: {q}")
            
            # --- THIS IS THE NEW LINE ---
            update_controller_q_table(agent)

if __name__ == "__main__":
    main()