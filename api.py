from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, AsyncGenerator
import asyncio
import json
from io import StringIO
from contextlib import asynccontextmanager
from browser_use import Agent, ChatOpenAI, Browser
from dotenv import load_dotenv
from blaxel.core.sandbox import SandboxInstance
import logging
import time
import os
import httpx

WEB_BROWSER_SANDBOX = {
    "sandbox": None,
    "cpd_url": None,
}

# Load environment variables
load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    sandbox = await SandboxInstance.create_if_not_exists({
        "name": f"browser-use-sandbox",
        "memory": 4096,
        "image": "sandbox/chromium-headless:latest",
        "region": "us-pdx-1",
    })
    preview = await sandbox.previews.create_if_not_exists({
        "metadata": {"name": "app-preview"},
        "spec": {
            "port": 443,
            "public": True,
        }
    })
    # Call the version endpoint to get the WebSocket debugger URL
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{preview.spec.url}/json/version")
        version_data = response.json()
        
        # Extract the ID from webSocketDebuggerUrl
        # Example: "wss://run.blaxel.ai/main/sandboxes/browser-use-sandbox-0/devtools/browser/388acf82-9c10-4726-8a1b-f165776d9443"
        ws_debugger_url = version_data.get("webSocketDebuggerUrl", "")
        
        # Extract the ID after "devtools/browser/"
        if "devtools/browser/" in ws_debugger_url:
            browser_id = ws_debugger_url.split("devtools/browser/")[-1]
            # Construct the WebSocket URL with the browser ID
            cpd_url = f"{preview.spec.url.replace('http', 'ws')}/devtools/browser/{browser_id}"
        else:
            # Fallback to the original URL if extraction fails
            cpd_url = preview.spec.url.replace("http", "ws")
    
    WEB_BROWSER_SANDBOX["sandbox"] = sandbox
    WEB_BROWSER_SANDBOX["cpd_url"] = cpd_url
    yield


        
# Initialize FastAPI app
app = FastAPI(
    title="Browser Agent API",
    description="API for running browser automation tasks with real-time log streaming",
    version="2.0.0",
    lifespan=lifespan
)


# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request models
class TaskRequest(BaseModel):
    task: str = Field(..., description="The task for the browser agent to perform")
    model: Optional[str] = Field(default="gemini-flash-latest", description="LLM model to use")
    
    class Config:
        json_schema_extra = {
            "example": {
                "task": "Find the number 1 post on Show HN",
                "model": "gemini-flash-latest"
            }
        }

# Custom logging handler for streaming
class StreamingLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.logs = []
        
    def emit(self, record):
        log_entry = self.format(record)
        self.logs.append(log_entry)
        
    def get_logs(self):
        """Get and clear accumulated logs"""
        logs = self.logs.copy()
        self.logs.clear()
        return logs

# Async generator for streaming agent execution
async def stream_agent_execution(task: str, model: str) -> AsyncGenerator[str, None]:
    """Stream the execution of a browser agent task"""
    
    # Send initial status
    yield f"data: {json.dumps({'type': 'status', 'message': 'Initializing agent...', 'timestamp': time.time()})}\n\n"
    
    # Create a custom logger for the agent
    logger = logging.getLogger('browser_use')
    logger.setLevel(logging.INFO)
    
    # Create streaming handler
    stream_handler = StreamingLogHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    
    # Capture stdout and stderr
    stdout_buffer = StringIO()
    stderr_buffer = StringIO()
    
    try:
        # Initialize the LLM and agent
        yield f"data: {json.dumps({'type': 'status', 'message': f'Setting up LLM with model: {model}', 'timestamp': time.time()})}\n\n"
        llm = ChatOpenAI(model=model, temperature=0.0, max_completion_tokens=2048)
        browser = Browser(
            headless=True,
            cdp_url=WEB_BROWSER_SANDBOX["cpd_url"]
            #cloud_browser=True
        )
       # yield f"data: {json.dumps({'type': 'status', 'message': f'Browser selected: {selected_browser["cpd_url"]}', 'timestamp': time.time()})}\n\n"
        yield f"data: {json.dumps({'type': 'status', 'message': f'Creating agent for task: {task}', 'timestamp': time.time()})}\n\n"
        agent = Agent(task=task, llm=llm, browser=browser, verbose=False)
        yield f"data: {json.dumps({'type': 'status', 'message': f'Agent created: {agent}', 'timestamp': time.time()})}\n\n"
        yield f"data: {json.dumps({'type': 'status', 'message': 'Starting agent execution...', 'timestamp': time.time()})}\n\n"
        
        # Create a task for the agent
        agent_task = asyncio.create_task(agent.run())
        
        # Stream logs while agent is running
        while not agent_task.done():
            # Check for new logs
            logs = stream_handler.get_logs()
            for log in logs:
                yield f"data: {json.dumps({'type': 'log', 'message': log, 'timestamp': time.time()})}\n\n"
            
            # Check stdout/stderr buffers
            stdout_content = stdout_buffer.getvalue()
            if stdout_content:
                for line in stdout_content.splitlines():
                    if line.strip():
                        yield f"data: {json.dumps({'type': 'stdout', 'message': line, 'timestamp': time.time()})}\n\n"
                stdout_buffer.truncate(0)
                stdout_buffer.seek(0)
            
            stderr_content = stderr_buffer.getvalue()
            if stderr_content:
                for line in stderr_content.splitlines():
                    if line.strip():
                        yield f"data: {json.dumps({'type': 'stderr', 'message': line, 'timestamp': time.time()})}\n\n"
                stderr_buffer.truncate(0)
                stderr_buffer.seek(0)
            
            # Small delay to prevent busy waiting
            await asyncio.sleep(0.1)
        
        # Get the result
        result = await agent_task
        
        # Send any remaining logs
        logs = stream_handler.get_logs()
        for log in logs:
            yield f"data: {json.dumps({'type': 'log', 'message': log, 'timestamp': time.time()})}\n\n"
        
        # Send success result
        yield f"data: {json.dumps({'type': 'result', 'status': 'success', 'data': str(result), 'timestamp': time.time()})}\n\n"
        yield f"data: {json.dumps({'type': 'complete', 'message': 'Agent execution completed successfully', 'timestamp': time.time()})}\n\n"
        
    except Exception as e:
        # Send error
        yield f"data: {json.dumps({'type': 'error', 'message': str(e), 'timestamp': time.time()})}\n\n"
        yield f"data: {json.dumps({'type': 'complete', 'message': 'Agent execution failed', 'timestamp': time.time()})}\n\n"
    
    finally:
        # Clean up
        logger.removeHandler(stream_handler)
        stdout_buffer.close()
        stderr_buffer.close()
        
        # Send end of stream
        yield f"data: {json.dumps({'type': 'end', 'timestamp': time.time()})}\n\n"

# API Endpoints
@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "name": "Browser Agent API",
        "version": "2.0.0",
        "endpoints": {
            "POST /run": "Run a browser agent task with real-time log streaming",
            "POST /run/json": "Run a task and get JSON response (no streaming)",
            "GET /health": "Health check endpoint"
        }
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": time.time()}

@app.post("/run")
async def run_agent_streaming(request: TaskRequest):
    """
    Run a browser agent task with real-time log streaming.
    Returns a Server-Sent Event stream with execution logs.
    
    The stream will include events of the following types:
    - status: Status updates about the execution
    - log: Log messages from the agent
    - stdout: Standard output from the agent
    - stderr: Standard error from the agent
    - result: The final result when successful
    - error: Error message if execution fails
    - complete: Indicates execution is complete
    - end: Final event to signal stream end
    """
    return StreamingResponse(
        stream_agent_execution(request.task, request.model),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering
        }
    )

@app.post("/run/json")
async def run_agent_json(request: TaskRequest):
    """
    Run a browser agent task and return the result as JSON.
    This endpoint waits for completion before returning.
    """
    try:
        # Initialize the LLM and agent
        llm = ChatOpenAI(model=request.model, temperature=0.0, max_completion_tokens=2048)
        browser = Browser(
            headless=True,
            cdp_url=WEB_BROWSER_SANDBOX["cpd_url"]
            #cloud_browser=True
        )
        agent = Agent(task=request.task, llm=llm, browser=browser, verbose=False)
        
        # Run the agent
        result = await agent.run()
        
        return JSONResponse(
            content={
                "status": "success",
                "task": request.task,
                "result": str(result),
                "timestamp": time.time()
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# HTML client for browser testing
HTML_CLIENT = '''
<!DOCTYPE html>
<html>
<head>
    <title>Browser Agent API - Live Stream</title>
    <style>
        body {
            font-family: monospace;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #1e1e1e;
            color: #d4d4d4;
        }
        h1 {
            color: #569cd6;
        }
        #form {
            margin-bottom: 20px;
            padding: 20px;
            background: #2d2d30;
            border-radius: 5px;
        }
        input, button {
            padding: 10px;
            margin: 5px;
            font-size: 16px;
            border-radius: 3px;
            border: 1px solid #3e3e42;
            background: #3c3c3c;
            color: #cccccc;
        }
        input {
            width: 500px;
        }
        button {
            cursor: pointer;
            background: #007acc;
            color: white;
        }
        button:hover {
            background: #005a9e;
        }
        button:disabled {
            background: #3c3c3c;
            cursor: not-allowed;
        }
        #output {
            background: #1e1e1e;
            border: 1px solid #3e3e42;
            border-radius: 5px;
            padding: 20px;
            height: 500px;
            overflow-y: auto;
            white-space: pre-wrap;
        }
        .status { color: #4ec9b0; }
        .log { color: #d4d4d4; }
        .stdout { color: #ce9178; }
        .stderr { color: #f48771; }
        .result { color: #b5cea8; font-weight: bold; }
        .error { color: #f48771; font-weight: bold; }
        .complete { color: #4ec9b0; font-weight: bold; }
        .time-info {
            color: #569cd6;
            font-weight: bold;
            margin: 10px 0;
            padding: 10px;
            background: #2d2d30;
            border-radius: 3px;
            border-left: 3px solid #007acc;
        }
    </style>
</head>
<body>
    <h1>Browser Agent API - Live Stream</h1>
    <div id="form">
        <input type="text" id="task" placeholder="Enter task (e.g., Find the number 1 post on Show HN)" value="Find the number 1 post on Show HN">
        <button onclick="runTask()" id="runButton">Run Task</button>
        <button onclick="clearOutput()">Clear Output</button>
        <div id="timeDisplay" style="display: none;" class="time-info"></div>
    </div>
    <div id="output"></div>
    
    <script>
        let eventSource = null;
        let requestStartTime = null;
        let requestEndTime = null;
        
        function formatDuration(milliseconds) {
            const seconds = Math.floor(milliseconds / 1000);
            const ms = milliseconds % 1000;
            if (seconds < 60) {
                return `${seconds}.${ms.toString().padStart(3, '0')}s`;
            }
            const minutes = Math.floor(seconds / 60);
            const remainingSeconds = seconds % 60;
            return `${minutes}m ${remainingSeconds}.${ms.toString().padStart(3, '0')}s`;
        }
        
        function updateTimeDisplay() {
            const timeDisplay = document.getElementById('timeDisplay');
            if (requestStartTime) {
                const currentTime = requestEndTime || Date.now();
                const elapsed = currentTime - requestStartTime;
                const status = requestEndTime ? 'Completed in' : 'Running for';
                timeDisplay.innerHTML = `⏱️ ${status}: ${formatDuration(elapsed)}`;
                timeDisplay.style.display = 'block';
            }
        }
        
        function runTask() {
            const task = document.getElementById('task').value;
            const output = document.getElementById('output');
            const button = document.getElementById('runButton');
            const timeDisplay = document.getElementById('timeDisplay');
            
            if (!task) {
                alert('Please enter a task');
                return;
            }
            
            // Initialize timing
            requestStartTime = Date.now();
            requestEndTime = null;
            
            // Start updating time display
            const timeInterval = setInterval(updateTimeDisplay, 100);
            
            // Disable button during execution
            button.disabled = true;
            
            // Clear previous output
            output.innerHTML = '';
            
            // Close any existing connection
            if (eventSource) {
                eventSource.close();
            }
            
            // Make POST request to get the stream (use current host)
            const apiUrl = window.location.origin + '/run';
            fetch(apiUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    task: task,
                    model: 'gpt-4o'
                })
            }).then(response => {
                // Create EventSource from the response
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                
                function processStream() {
                    reader.read().then(({done, value}) => {
                        if (done) {
                            requestEndTime = Date.now();
                            clearInterval(timeInterval);
                            updateTimeDisplay();
                            button.disabled = false;
                            return;
                        }
                        
                        const text = decoder.decode(value);
                        const lines = text.split('\\n');
                        
                        for (const line of lines) {
                            if (line.startsWith('data: ')) {
                                try {
                                    const data = JSON.parse(line.substring(6));
                                    const timestamp = new Date(data.timestamp * 1000).toLocaleTimeString();
                                    
                                    let className = data.type;
                                    let prefix = `[${timestamp}] [${data.type.toUpperCase()}]`;
                                    let message = data.message || data.data || '';
                                    
                                    if (data.type === 'end') {
                                        requestEndTime = Date.now();
                                        clearInterval(timeInterval);
                                        updateTimeDisplay();
                                        button.disabled = false;
                                        output.innerHTML += `<span class="complete">${prefix} Stream ended</span>\\n`;
                                        output.innerHTML += `<span class="time-info">⏱️ Total time: ${formatDuration(requestEndTime - requestStartTime)}</span>\\n`;
                                        return;
                                    }
                                    
                                    output.innerHTML += `<span class="${className}">${prefix} ${message}</span>\\n`;
                                    output.scrollTop = output.scrollHeight;
                                } catch (e) {
                                    console.error('Failed to parse:', line);
                                }
                            }
                        }
                        
                        processStream();
                    });
                }
                
                processStream();
            }).catch(error => {
                requestEndTime = Date.now();
                clearInterval(timeInterval);
                updateTimeDisplay();
                output.innerHTML += `<span class="error">[ERROR] ${error}</span>\\n`;
                if (requestStartTime) {
                    output.innerHTML += `<span class="time-info">⏱️ Failed after: ${formatDuration(requestEndTime - requestStartTime)}</span>\\n`;
                }
                button.disabled = false;
            });
        }
        
        function clearOutput() {
            document.getElementById('output').innerHTML = '';
            document.getElementById('timeDisplay').style.display = 'none';
            requestStartTime = null;
            requestEndTime = null;
        }
        
        // Allow Enter key to run task
        document.getElementById('task').addEventListener('keypress', function(e) {
            if (e.key === 'Enter' && !document.getElementById('runButton').disabled) {
                runTask();
            }
        });
    </script>
</body>
</html>
'''

@app.get("/test")
async def test_page():
    """Serve a test HTML page for the streaming API"""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=HTML_CLIENT)

if __name__ == "__main__":
    import uvicorn
    import os
    
    # Get host and port from Blaxel environment variables
    port = int(os.environ.get("BL_SERVER_PORT", "80"))
    host = os.environ.get("BL_SERVER_HOST", "0.0.0.0")
    print(f"Starting server on http://{host}:{port}")
    print(f"Test the streaming API at http://{host if host != '0.0.0.0' else 'localhost'}:{port}/test")
    print("Or use the test_client.py file")
    
    uvicorn.run(app, host=host, port=port)


