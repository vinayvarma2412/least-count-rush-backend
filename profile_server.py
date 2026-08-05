import asyncio
import psutil
import subprocess
import time
import os
import websockets
import sys

PORT = 8123
URL = f"ws://127.0.0.1:{PORT}/ws/test_room_bulk"
NUM_CONNECTIONS = 500

async def measure_memory_and_cpu():
    # Start server
    print("Starting server...")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    try:
        # Wait for server to start
        time.sleep(3)
        
        # Get process object
        ps_proc = psutil.Process(process.pid)
        
        # Baseline memory
        base_mem = ps_proc.memory_info().rss / 1024 / 1024
        print(f"Baseline Memory (Idle): {base_mem:.2f} MB")
        
        # CPU Baseline
        ps_proc.cpu_percent(interval=1.0) # warm up
        base_cpu = ps_proc.cpu_percent(interval=1.0)
        print(f"Baseline CPU (Idle): {base_cpu:.2f}%")
        
        print(f"Connecting {NUM_CONNECTIONS} websockets...")
        connections = []
        for i in range(NUM_CONNECTIONS):
            try:
                # Need unique rooms to simulate actual load, or same room? Same room is fine for now, or spread it.
                ws = await websockets.connect(URL + str(i % 10))
                connections.append(ws)
            except Exception as e:
                print(f"Failed to connect: {e}")
                break
                
        # Wait a bit for connections to stabilize
        time.sleep(2)
        
        # Loaded memory
        loaded_mem = ps_proc.memory_info().rss / 1024 / 1024
        print(f"Memory with {len(connections)} connections: {loaded_mem:.2f} MB")
        
        mem_per_conn = (loaded_mem - base_mem) / max(1, len(connections))
        print(f"Memory per connection: {mem_per_conn:.2f} MB / {mem_per_conn*1024:.2f} KB")
        
        # Loaded CPU (just maintaining connections)
        loaded_cpu = ps_proc.cpu_percent(interval=1.0)
        print(f"CPU with {len(connections)} idle connections: {loaded_cpu:.2f}%")
        
        # Close connections gracefully
        for ws in connections:
            await ws.close()
            
    finally:
        print("Terminating server...")
        process.terminate()
        process.wait()

if __name__ == "__main__":
    asyncio.run(measure_memory_and_cpu())
