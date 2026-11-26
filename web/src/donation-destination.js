import { esc } from './ui.js';

const option = ({ id, name }) => `<option value="${esc(id)}">${esc(name)}</option>`;
const unknownOption = '<option value="">未记录</option>';

export function donationDestinationFields(recipients, { optional = false } = {}) {
  if (!recipients?.length) return '';
  const prefix = optional ? 'adminDonation' : 'donation';
  return `<div class="cm-donate-destination">
    <div>
      <label for="${prefix}Recipient">收款对象</label>
      <select id="${prefix}Recipient" data-donation-recipient>
        ${optional ? unknownOption : ''}${recipients.map(option).join('')}
      </select>
    </div>
    <div>
      <label for="${prefix}Method">支付方式</label>
      <select id="${prefix}Method" data-donation-method></select>
    </div>
  </div>`;
}

export function bindDonationDestination(root, recipients, { optional = false, onChange } = {}) {
  const recipientSelect = root.querySelector('[data-donation-recipient]');
  if (!recipientSelect) return () => ({});
  const methodSelect = root.querySelector('[data-donation-method]');
  const selectedRecipient = () => recipients.find(recipient => recipient.id === recipientSelect.value);
  const notify = () => {
    const recipient = selectedRecipient();
    onChange?.(recipient, recipient?.methods.find(method => method.id === methodSelect.value));
  };
  const updateMethods = () => {
    const methods = selectedRecipient()?.methods || [];
    methodSelect.innerHTML = (optional ? unknownOption : '') + methods.map(option).join('');
    methodSelect.disabled = !methods.length;
    notify();
  };
  recipientSelect.addEventListener('change', updateMethods);
  methodSelect.addEventListener('change', notify);
  updateMethods();
  return () => ({
    recipientId: recipientSelect.value || undefined,
    paymentMethod: methodSelect.value || undefined,
  });
}

export function donationDestinationLabel(recipients, record) {
  const recipient = recipients?.find(entry => entry.id === record.recipient_id);
  const method = recipient?.methods.find(entry => entry.id === record.payment_method);
  return `收款对象 ${recipient?.name || record.recipient_id || '未记录'} · 支付方式 ${method?.name || record.payment_method || '未记录'}`;
}
