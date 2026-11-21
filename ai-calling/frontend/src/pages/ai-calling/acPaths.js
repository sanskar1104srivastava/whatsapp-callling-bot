// One place for the route paths, so moving the section inside Rekon-AI is a single edit.
export const AC = "/ai-calling";
export const P = {
  home: AC,
  usecases: `${AC}/inbound/usecases`,
  usecaseNew: `${AC}/inbound/usecases/new`,
  usecase: (id) => `${AC}/inbound/usecases/${id}`,
  routing: `${AC}/inbound/routing`,
  conversations: `${AC}/inbound/conversations`,
  outbound: `${AC}/outbound`,
};
