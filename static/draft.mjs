// Unknown extracted values must remain unknown until a person confirms them.
export function draftField(review, name) {
  if (review !== null && review !== undefined) return review[name] ?? '';
  if (name === 'tax' || name === 'discount') return '0';
  return name === 'currency' ? 'PKR' : '';
}
