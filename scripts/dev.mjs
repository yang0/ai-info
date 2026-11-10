import {spawn} from 'node:child_process';
const args=process.argv.slice(2);
const option=(name,fallback)=>{const i=args.indexOf(name);return i>=0?args[i+1]:fallback;};
const child=spawn(process.env.PYTHON_BIN||'python',['-m','intel','--host',option('--host','127.0.0.1'),'--port',option('--port',process.env.PORT||'8766')],{stdio:'inherit'});
child.on('error',error=>{process.stderr.write(`Could not start Python: ${error.message}\n`);process.exitCode=1;});
child.on('exit',code=>{process.exitCode=code??1;});
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>child.kill(signal));
