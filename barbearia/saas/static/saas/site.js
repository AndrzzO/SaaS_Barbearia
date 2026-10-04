"use strict";
const info = {
 contato: ["Contato", "O canal oficial de atendimento da GTS será disponibilizado aqui."],
 redes: ["Siga a GTS", "Os perfis oficiais da Guild Tech Support serão disponibilizados aqui."],
 sobre: ["Guild Tech Support", "Tecnologia que mantém sua barbearia em movimento. Gestão de filas, cadeiras e atendimento em um só lugar."],
 privacidade: ["Privacidade", "A política de privacidade será disponibilizada antes da abertura comercial do serviço."],
 termos: ["Termos de uso", "Os termos de uso serão disponibilizados antes da abertura comercial do serviço."]
};
const dialog = document.querySelector("#informacao");
document.querySelectorAll("[data-info]").forEach(a => a.addEventListener("click", e => {
 e.preventDefault(); const [title, text] = info[a.dataset.info];
 document.querySelector("#info-titulo").textContent = title;
 document.querySelector("#info-texto").textContent = text;
 dialog.showModal();
}));
dialog?.querySelector(".dialog-close").addEventListener("click", () => dialog.close());
dialog?.addEventListener("click", e => { if(e.target === dialog) { const r=dialog.getBoundingClientRect(); if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom) dialog.close(); } });
const feedback = (root, text) => { const node=root.querySelector("[data-feedback]"); if(node){node.textContent=text;node.hidden=false;} };
const csrf = () => document.cookie.split("; ").find(v=>v.startsWith("csrftoken="))?.split("=")[1] || "";
async function post(url, body, json=false) {
 const headers={"Accept":"application/json","X-CSRFToken":decodeURIComponent(csrf())};
 if(json) headers["Content-Type"]="application/json";
 const response=await fetch(url,{method:"POST",headers,body:json?JSON.stringify(body):body,credentials:"same-origin"});
 if(!(response.headers.get("content-type")||"").includes("application/json")) throw new Error("Não foi possível concluir agora. Atualize a página e tente novamente.");
 const data=await response.json();
 if(!response.ok) throw new Error(data.mensagem || (data.erro && !/^[A-Z_]+$/.test(data.erro)?data.erro:"Não foi possível concluir agora. Consulte sua conta e tente novamente."));
 return data;
}
document.querySelector("[data-checkout-form]")?.addEventListener("submit", async e => {
 e.preventDefault(); const form=e.currentTarget, button=form.querySelector("button"), original=button.textContent;
 button.disabled=true;button.textContent="Aguarde…";
 try{ const data=await post(form.action,new FormData(form)); window.location.assign(data.redirecionar||data.url); }
 catch(error){feedback(form.closest(".plan-card"),error.message);button.disabled=false;button.textContent=original;}
});
document.querySelector("[data-onboarding]")?.addEventListener("submit",async e=>{
 e.preventDefault(); const form=e.currentTarget,button=form.querySelector("button");button.disabled=true;
 try{const values=Object.fromEntries(new FormData(form));delete values.csrfmiddlewaretoken;await post(form.action,values,true);window.location.assign("/planos/");}
 catch(error){feedback(form,error.message);button.disabled=false;}
});
document.querySelector("#slug")?.addEventListener("input",e=>{document.querySelector("[data-slug-preview]").textContent=e.target.value||"minha-barbearia";});
document.querySelector("[data-portal]")?.addEventListener("click",async e=>{
 const button=e.currentTarget;button.disabled=true;
 try{const data=await post("/conta/portal/",{} ,true);window.location.assign(data.url);}
 catch(error){feedback(document.querySelector(".account-card"),error.message);button.disabled=false;}
});
const stateNode=document.querySelector("#estado-conta");
if(stateNode){
 const state=JSON.parse(stateNode.textContent);
 const descriptions={
 SEM_ASSINATURA:"Seu estabelecimento está cadastrado. Escolha o plano mensal para continuar.",
 CHECKOUT_PENDENTE:"Sua contratação está pendente. Continue o pagamento ou aguarde a confirmação.",
 PAGAMENTO_PENDENTE:"Pagamento em processamento ou com ação adicional necessária. Consulte sua assinatura.",
 PROVISIONAMENTO_PENDENTE:"Pagamento confirmado. Estamos preparando seu estabelecimento.",
 CONFIGURAR_EQUIPE:"Sua assinatura está válida. Falta configurar a equipe para iniciar a operação.",
 ATIVA:"Sua assinatura está ativa.",
 TOLERANCIA:"Há um pagamento em atraso. Regularize sua assinatura para manter a operação.",
 CANCELAMENTO_AGENDADO:"Cancelamento agendado. O acesso continua até o fim do período pago.",
 ENCERRADA:"Sua assinatura foi encerrada. Você pode contratar novamente para a mesma barbearia.",
 SUSPENSA:"A operação está suspensa. Regularize sua assinatura para continuar.",
 BLOQUEIO_ADMINISTRATIVO:"O estabelecimento está bloqueado administrativamente. Entre em contato com a GTS."
 };
 const status=document.querySelector("[data-account-status]");if(status)status.textContent=descriptions[state.estado]||"Consulte a situação da sua conta.";
 document.querySelectorAll("[data-date]").forEach(node=>{const d=new Date(node.dataset.date);if(!isNaN(d))node.textContent=new Intl.DateTimeFormat("pt-BR",{dateStyle:"long"}).format(d);});
}
